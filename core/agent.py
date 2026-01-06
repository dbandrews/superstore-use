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

# Modal app name for looking up deployed functions
MODAL_APP_NAME = "superstore-agent"

# System prompt for the grocery shopping assistant
SYSTEM_PROMPT = """You are a helpful grocery shopping assistant for Real Canadian Superstore.

Your capabilities:
1. **Understanding Requests**: When users describe what they want to cook or buy, extract the specific grocery items needed.
2. **Adding to Cart**: Use the add_items_to_cart tool to add items. Login is handled automatically.
3. **View Cart**: Use view_cart to check what's currently in the cart.

Guidelines:
- When a user says something like "I want to make pasta carbonara", extract the ingredients:
  eggs, parmesan cheese, pancetta or bacon, spaghetti, black pepper, etc.
- Always confirm the items with the user before adding to cart.
- Be helpful and suggest common items that might be needed.
- If items fail to add, suggest alternatives.
- Keep responses concise and friendly.

Example interactions:

User: "I want to make pancakes"
You: "For pancakes, you'll need:
- All-purpose flour
- Milk
- Eggs
- Butter
- Baking powder
- Sugar
- Maple syrup

Would you like me to add these to your cart?"

User: "Yes, add them"
You: [Uses add_items_to_cart with the list - login is automatic]

IMPORTANT:
- When adding items, use simple search-friendly names like "milk", "eggs", "butter" rather than overly specific descriptions.
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
            f"Error: Modal app '{MODAL_APP_NAME}' not found. "
            "Please deploy it first with: modal deploy modal_app.py"
        )
    except Exception as e:
        print(f"[Agent] Login error: {e}")
        return False, f"Login error: {str(e)}"


def add_items_to_cart_streaming(items: list[str]) -> Generator[dict, None, str]:
    """
    Streaming version of add_items_to_cart that yields progress events.

    Uses Modal's starmap for PARALLEL execution across containers,
    yielding progress events as each item completes.

    Yields:
        dict: Progress events with types:
            - {"type": "status", "message": str}
            - {"type": "item_start", "item": str, "index": int, "total": int}
            - {"type": "item_complete", "item": str, "status": str, "message": str, ...}
            - {"type": "complete", "success_count": int, "failure_count": int, "message": str}
            - {"type": "error", "message": str}

    Returns:
        str: Final summary message
    """
    if not items:
        yield {"type": "error", "message": "No items provided"}
        return "No items provided to add to cart."

    # Ensure logged in before adding items
    global _logged_in
    if _logged_in:
        yield {"type": "status", "message": "Already logged in"}
    else:
        yield {"type": "status", "message": "Logging in to Superstore... (this may take a minute)"}

    login_ok, login_msg = _ensure_logged_in()
    if not login_ok:
        yield {"type": "error", "message": f"Login failed: {login_msg}"}
        return f"Cannot add items: {login_msg}"

    total = len(items)

    # Emit status first, then item_start events (so items display isn't wiped out by status)
    yield {
        "type": "status",
        "message": f"Adding {total} items in parallel...",
        "total": total,
    }

    # Emit item_start events for all items upfront (they'll process in parallel)
    for i, item in enumerate(items, 1):
        yield {
            "type": "item_start",
            "item": item,
            "index": i,
            "total": total,
            "started": i,
        }

    try:
        # Use the non-streaming version with starmap for parallel execution
        # (Modal's starmap doesn't support generator functions)
        add_fn = get_modal_function("add_item_remote")

        # Prepare inputs for starmap: [(item, index), ...]
        inputs = [(item, i) for i, item in enumerate(items, 1)]

        results = []
        completed_count = 0

        # Process items in PARALLEL - results come back as containers complete
        for result in add_fn.starmap(inputs, order_outputs=False):
            completed_count += 1
            results.append(result)

            yield {
                "type": "item_complete",
                "item": result.get("item", "?"),
                "index": result.get("index", 0),
                "total": total,
                "completed": completed_count,
                "status": result.get("status", "unknown"),
                "message": result.get("message", ""),
                "steps": result.get("steps", 0),
            }

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
            failed_items = ", ".join(
                f"{r.get('item', '?')} ({r.get('message', 'error')[:50]})"
                for r in failures
            )
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
            f"Error: Modal app '{MODAL_APP_NAME}' not found. "
            "Please deploy it first with: modal deploy modal_app.py"
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
        "Cart viewing is not yet available via Modal. "
        "Please check the Superstore website directly to view your cart."
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
    # Create the LLM with streaming-aware tools bound
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    llm_with_tools = llm.bind_tools(STREAMING_TOOLS)

    def chat_node(state: GroceryState):
        """Main chat node that processes user messages."""
        messages = state["messages"]

        # Add system prompt if not present
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)

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
            result = agent.invoke(
                {"messages": [HumanMessage(content=user_input)]}, config=config
            )

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
