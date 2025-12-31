"""
LangGraph-based Chat Agent for Grocery Shopping.

This agent handles natural language conversation with users, extracts grocery items
from their requests, and coordinates with Modal-based browser automation to add
items to cart.

Supports streaming progress events when used with stream_mode=["custom", ...].
"""

from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from modal_tools import add_items_to_cart_streaming, view_cart

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

    print("\n🛒 Grocery Shopping Chat Agent")
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
