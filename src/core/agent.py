"""
LangGraph-based Chat Agent for Grocery Shopping.

This agent handles natural language conversation with users, extracts grocery items
from their requests, and coordinates with Modal-based browser automation to add
items to cart.

Supports streaming progress events when used with stream_mode=["custom", ...].
"""

from typing import Generator, Literal

import modal
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.core.config import load_config

# Load configuration
_config = load_config()

# Modal app name for looking up deployed functions
MODAL_APP_NAME = _config.app.name


def _get_system_prompt() -> str:
    """Load system prompt from file, with fallback to default."""
    try:
        return _config.load_prompt("chat_system")
    except FileNotFoundError:
        # Fallback to inline prompt if file not found
        return """You are a helpful grocery shopping assistant for Real Canadian Superstore.

Your capabilities:
1. **Understanding Requests**: When users describe what they want to cook or buy, extract the specific grocery items needed WITH QUANTITIES.
2. **Adding to Cart**: Use the add_items_to_cart tool to add items. Login is handled automatically.
3. **View Cart**: Use view_cart to check what's currently in the cart.

Guidelines:
- ALWAYS include quantities in your item lists.
- Always confirm the items with the user before adding to cart.
- Be helpful and suggest common items that might be needed.
- If items fail to add, suggest alternatives.
- Keep responses concise and friendly.

IMPORTANT:
- ALWAYS include quantities in item names passed to add_items_to_cart (e.g., "6 apples", "2 liters milk", "500g chicken breast")
- Use simple, search-friendly descriptions with quantities
- After adding items, summarize what was added and what failed.
"""

# Track login state for the session
_logged_in = False


def get_modal_function(function_name: str) -> modal.Function:
    """Look up a deployed Modal function."""
    return modal.Function.from_name(MODAL_APP_NAME, function_name)


def _ensure_logged_in() -> tuple[bool, str]:
    """Ensure user is logged in. Returns (success, message)."""
    global _logged_in

    if _logged_in:
        return True, "Already logged in."

    print("\n[Agent] Logging in to Superstore...")

    try:
        login_fn = get_modal_function("login_remote")
        result = login_fn.remote()

        if result.get("status") == "success":
            _logged_in = True
            print("[Agent] Login successful!")
            return True, "Login successful."
        else:
            error_msg = result.get("message", "Unknown error")
            print(f"[Agent] Login failed: {error_msg}")
            return False, f"Login failed: {error_msg}"
    except modal.exception.NotFoundError:
        return False, (
            f"Error: Modal app '{MODAL_APP_NAME}' not found. Please deploy it first with: modal deploy modal_app.py"
        )
    except Exception as e:
        print(f"[Agent] Login error: {e}")
        return False, f"Login error: {str(e)}"


def _ensure_logged_in_streaming() -> Generator[dict, None, tuple[bool, str]]:
    """
    Streaming version of login that yields progress events.

    Yields:
        dict: Progress events with types:
            - {"type": "login_step", "step": int, "thinking": str, "next_goal": str}
            - {"type": "login_complete", "status": str, "message": str, "steps": int}

    Returns:
        tuple[bool, str]: (success, message)
    """
    import json

    global _logged_in

    if _logged_in:
        yield {"type": "login_complete", "status": "success", "message": "Already logged in", "steps": 0}
        return True, "Already logged in."

    print("\n[Agent] Logging in to Superstore (streaming)...")

    try:
        login_fn = get_modal_function("login_remote_streaming")

        for event_json in login_fn.remote_gen():
            event = json.loads(event_json)
            event_type = event.get("type")

            if event_type == "start":
                yield {"type": "login_start"}
            elif event_type == "step":
                yield {
                    "type": "login_step",
                    "step": event.get("step", 0),
                    "thinking": event.get("thinking"),
                    "next_goal": event.get("next_goal"),
                }
            elif event_type == "complete":
                status = event.get("status", "failed")
                message = event.get("message", "Unknown")
                steps = event.get("steps", 0)

                yield {
                    "type": "login_complete",
                    "status": status,
                    "message": message,
                    "steps": steps,
                }

                if status == "success":
                    _logged_in = True
                    print(f"[Agent] Login successful! ({steps} steps)")
                    return True, "Login successful."
                else:
                    print(f"[Agent] Login failed: {message}")
                    return False, f"Login failed: {message}"

        # If we exit the loop without a complete event
        return False, "Login stream ended unexpectedly"

    except modal.exception.NotFoundError:
        error_msg = (
            f"Error: Modal app '{MODAL_APP_NAME}' not found. Please deploy it first with: modal deploy modal_app.py"
        )
        yield {"type": "login_complete", "status": "failed", "message": error_msg, "steps": 0}
        return False, error_msg
    except Exception as e:
        print(f"[Agent] Login error: {e}")
        error_msg = f"Login error: {str(e)}"
        yield {"type": "login_complete", "status": "failed", "message": error_msg, "steps": 0}
        return False, error_msg


def add_items_to_cart_streaming(items: list[str]) -> Generator[dict, None, str]:
    """
    Streaming version of add_items_to_cart that yields real-time progress events.

    Uses parallel threads to consume streaming generators from Modal containers,
    yielding step-by-step progress events as each browser agent works.

    Yields:
        dict: Progress events with types:
            - {"type": "status", "message": str}
            - {"type": "item_start", "item": str, "index": int, "total": int}
            - {"type": "step", "item": str, "index": int, "step": int, "action": str}
            - {"type": "item_complete", "item": str, "status": str, "message": str, ...}
            - {"type": "complete", "success_count": int, "failure_count": int, "message": str}
            - {"type": "error", "message": str}

    Returns:
        str: Final summary message
    """
    import json
    import queue
    from concurrent.futures import ThreadPoolExecutor

    if not items:
        yield {"type": "error", "message": "No items provided"}
        return "No items provided to add to cart."

    # Ensure logged in before adding items - use streaming for progress updates
    global _logged_in
    if _logged_in:
        yield {"type": "status", "message": "Already logged in"}
    else:
        yield {"type": "status", "message": "Logging in to Superstore..."}

    # Use streaming login and forward all events
    login_gen = _ensure_logged_in_streaming()
    login_ok = False
    login_msg = ""

    for event in login_gen:
        yield event  # Forward login progress events to caller
        if event.get("type") == "login_complete":
            login_ok = event.get("status") == "success"
            login_msg = event.get("message", "")

    if not login_ok:
        yield {"type": "error", "message": f"Login failed: {login_msg}"}
        return f"Cannot add items: {login_msg}"

    total = len(items)

    yield {
        "type": "status",
        "message": f"Adding {total} items in parallel with real-time progress...",
        "total": total,
    }

    # Shared queue for collecting events from all streaming containers
    event_queue: queue.Queue[dict] = queue.Queue()

    def consume_stream(item: str, index: int):
        """Consume streaming events from a single container and put them on the queue."""
        try:
            add_fn = get_modal_function("add_item_remote_streaming")
            for event_json in add_fn.remote_gen(item, index):
                event = json.loads(event_json)
                event_queue.put(event)
        except Exception as e:
            # If streaming fails, emit a failure event
            event_queue.put(
                {
                    "type": "complete",
                    "item": item,
                    "index": index,
                    "status": "failed",
                    "message": str(e),
                    "steps": 0,
                }
            )

    try:
        # Start all streams in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(items)) as executor:
            futures = [executor.submit(consume_stream, item, i) for i, item in enumerate(items, 1)]

            results = []
            completed_count = 0

            # Collect events until all items complete
            while completed_count < total:
                try:
                    event = event_queue.get(timeout=1.0)
                    event_type = event.get("type")

                    if event_type == "start":
                        # Transform to item_start for consistency
                        yield {
                            "type": "item_start",
                            "item": event["item"],
                            "index": event["index"],
                            "total": total,
                            "started": event["index"],
                        }
                    elif event_type == "step":
                        # Forward step events directly for real-time progress
                        yield event
                    elif event_type == "complete":
                        completed_count += 1
                        results.append(event)
                        yield {
                            "type": "item_complete",
                            "item": event.get("item", "?"),
                            "index": event.get("index", 0),
                            "total": total,
                            "completed": completed_count,
                            "status": event.get("status", "unknown"),
                            "message": event.get("message", ""),
                            "steps": event.get("steps", 0),
                        }
                except queue.Empty:
                    # Check if all futures are done (handles edge cases)
                    if all(f.done() for f in futures):
                        break

            # Wait for any remaining futures to complete
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass  # Errors already handled in consume_stream

        # Calculate summary
        successes = [r for r in results if r.get("status") == "success"]
        failures = [r for r in results if r.get("status") == "failed"]
        uncertain = [r for r in results if r.get("status") == "uncertain"]

        summary = f"Added {len(successes)}/{len(items)} items to cart."
        if successes:
            success_items = ", ".join(r.get("item", "?") for r in successes)
            summary += f"\n\nSuccessfully added: {success_items}"
        if uncertain:
            uncertain_items = ", ".join(r.get("item", "?") for r in uncertain)
            summary += f"\n\nUncertain (may have been added): {uncertain_items}"
        if failures:
            failed_items = ", ".join(f"{r.get('item', '?')} ({r.get('message', 'error')[:50]})" for r in failures)
            summary += f"\n\nFailed to add: {failed_items}"

        yield {
            "type": "complete",
            "success_count": len(successes),
            "failure_count": len(failures),
            "uncertain_count": len(uncertain),
            "message": summary,
        }

        return summary

    except modal.exception.NotFoundError:
        error_msg = (
            f"Error: Modal app '{MODAL_APP_NAME}' not found. Please deploy it first with: modal deploy modal_app.py"
        )
        yield {"type": "error", "message": error_msg}
        return error_msg
    except Exception as e:
        error_msg = f"Error adding items: {str(e)}"
        yield {"type": "error", "message": error_msg}
        return error_msg


@tool
def add_items_to_cart(items: list[str]) -> str:
    """
    Add grocery items to the Real Canadian Superstore cart.

    Items are added in parallel using Modal containers for efficiency.
    Automatically handles login if not already logged in.

    When used with stream_mode=["custom", ...], emits progress events
    for each item being processed.

    Args:
        items: List of grocery items to add (e.g., ["milk", "eggs", "bread"])

    Returns:
        Summary of which items were added successfully and which failed.
    """
    # Get stream writer for emitting custom progress events
    writer = get_stream_writer()

    final_summary = ""

    # Call the streaming version and emit progress events
    for event in add_items_to_cart_streaming(items):
        # Emit progress to the stream if streaming is enabled
        if writer:
            writer({"progress": event})

        # Capture the final summary
        if event.get("type") == "complete":
            final_summary = event.get("message", "")
        elif event.get("type") == "error":
            final_summary = event.get("message", "Error occurred")

    return final_summary or "Completed processing items."


@tool
def view_cart() -> str:
    """
    View the current cart contents at Real Canadian Superstore.

    Note: This tool is not yet implemented for Modal.

    Returns:
        Description of items currently in cart.
    """
    return (
        "Cart viewing is not yet available via Modal. Please check the Superstore website directly to view your cart."
    )


# Streaming-aware tools for the agent
STREAMING_TOOLS = [add_items_to_cart, view_cart]


class GroceryState(MessagesState):
    """State for the grocery shopping agent."""

    pass


def create_chat_agent():
    """Create and return the grocery shopping chat agent.

    The agent supports streaming when invoked with:
        agent.astream(inputs, config, stream_mode=["updates", "custom"])

    Custom stream events are emitted for item processing progress.
    """
    config = load_config()

    # Create the LLM with streaming-aware tools bound
    llm = ChatOpenAI(
        model=config.llm.chat_model,
        temperature=config.llm.chat_temperature,
    )
    llm_with_tools = llm.bind_tools(STREAMING_TOOLS)

    # Load system prompt
    system_prompt = _get_system_prompt()

    def chat_node(state: GroceryState):
        """Main chat node that processes user messages."""
        messages = state["messages"]

        # Add system prompt if not present
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=system_prompt)] + list(messages)

        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: GroceryState) -> Literal["tools", "__end__"]:
        """Determine if we should continue to tools or end."""
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return "__end__"

    # Build the graph
    workflow = StateGraph(GroceryState)

    # Add nodes
    workflow.add_node("chat", chat_node)
    workflow.add_node("tools", ToolNode(STREAMING_TOOLS))

    # Add edges
    workflow.add_edge(START, "chat")
    workflow.add_conditional_edges("chat", should_continue)
    workflow.add_edge("tools", "chat")

    # Compile with checkpointer for conversation persistence
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def run_cli():
    """Run the agent in CLI mode for testing."""
    from dotenv import load_dotenv

    load_dotenv()

    agent = create_chat_agent()
    config = {"configurable": {"thread_id": "cli-session-1"}}

    print("\n[Grocery Shopping Chat Agent]")
    print("=" * 50)
    print("I can help you order groceries from Real Canadian Superstore.")
    print("Tell me what you'd like to make or buy!")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "bye"]:
                print("Goodbye!")
                break

            # Invoke the agent
            result = agent.invoke({"messages": [HumanMessage(content=user_input)]}, config=config)

            # Print the assistant's response
            last_message = result["messages"][-1]
            if isinstance(last_message, AIMessage):
                print(f"\nAssistant: {last_message.content}\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    run_cli()
