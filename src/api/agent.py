"""API-based Chat Agent for Grocery Shopping.

This agent uses direct API calls instead of browser automation for
product search and cart operations, providing much faster responses.

Browser automation is only used for initial login (if needed).
"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.core.config import load_config
from src.api.tools import API_TOOLS, set_credentials, initialize_anonymous_session
from src.api.credentials import SuperstoreCredentials


API_SYSTEM_PROMPT = """You are a helpful grocery shopping assistant for Real Canadian Superstore.

## Your Capabilities (API-Based - Very Fast!)
1. **Search Products**: Use search_products to find items by name
2. **Add to Cart**: Use add_item_to_cart with the product CODE from search results
3. **View Cart**: Use view_cart to see what's in the cart

## CRITICAL WORKFLOW:
1. When user wants to add items, ALWAYS search first to get the product CODE
2. The product code is required - it looks like "20657896_EA" or "20132621001_KG"
3. Present search results to the user and let them choose which product
4. Use the exact code from search results when calling add_item_to_cart

## Example Flow:
User: "Add milk to my cart"
You: Let me search for milk products...
[Call search_products("milk")]
[Show results with codes and prices]
"I found several milk options. Which would you like me to add?"

User: "Add the first one"
[Call add_item_to_cart with the code from result #1]
"Done! I've added [product name] to your cart."

## Guidelines:
- Always search before adding - this confirms the product exists and shows options
- Show search results to users so they can pick the right product
- Include quantities when adding (default is 1)
- Use view_cart periodically to confirm items were added
- Be concise but helpful
- If a product isn't found, suggest alternative search terms

## Important Notes:
- Product codes have suffixes: _EA (each), _KG (by weight), _C12 (case of 12)
- Prices shown are current for the selected store
- Cart persists between messages in this session
"""


class GroceryState(MessagesState):
    """State for the grocery shopping agent."""

    pass


def create_api_agent(
    credentials: SuperstoreCredentials | None = None,
    model: str | None = None,
    temperature: float | None = None,
):
    """Create the API-based grocery shopping chat agent.

    This agent uses direct API calls for search and cart operations,
    making it much faster than browser-based automation.

    Args:
        credentials: Pre-existing credentials from login. If None,
            an anonymous session will be created on first tool use.
        model: LLM model to use. Defaults to config value.
        temperature: LLM temperature. Defaults to config value.

    Returns:
        Compiled LangGraph agent
    """
    config = load_config()

    # Initialize API client with credentials if provided
    if credentials is not None:
        set_credentials(credentials)

    # Create the LLM with tools bound
    llm = ChatGroq(
        model=model or config.llm.chat_model,
        temperature=temperature if temperature is not None else config.llm.chat_temperature,
    )
    llm_with_tools = llm.bind_tools(API_TOOLS)

    def chat_node(state: GroceryState):
        """Main chat node that processes user messages."""
        messages = state["messages"]

        # Add system prompt if not present
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=API_SYSTEM_PROMPT)] + list(messages)

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
    workflow.add_node("tools", ToolNode(API_TOOLS))

    # Add edges
    workflow.add_edge(START, "chat")
    workflow.add_conditional_edges("chat", should_continue)
    workflow.add_edge("tools", "chat")

    # Compile with checkpointer for conversation persistence
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


async def run_cli():
    """Run the API-based agent in CLI mode for testing."""
    from dotenv import load_dotenv
    import asyncio

    load_dotenv()

    print("\n[API-Based Grocery Shopping Agent]")
    print("=" * 50)
    print("Initializing anonymous shopping session...")

    # Initialize anonymous session (no login needed)
    credentials = await initialize_anonymous_session()
    print(f"Cart created: {credentials.cart_id[:8]}...")
    print(f"Store: {credentials.store_id}")
    print()
    print("I can help you shop for groceries at Real Canadian Superstore.")
    print("Try: 'search for milk' or 'find chicken breast'")
    print("Type 'quit' to exit.\n")

    agent = create_api_agent(credentials)
    config = {"configurable": {"thread_id": "cli-session-1"}}

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "bye"]:
                print("Goodbye!")
                break

            # Invoke the agent (use ainvoke for async tools)
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
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


def main():
    """Entry point for CLI."""
    import asyncio
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
