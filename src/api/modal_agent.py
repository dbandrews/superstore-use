"""API-based Chat Agent for Modal deployment.

This agent uses the Modal-deployed API functions for product search
and cart operations, providing much faster responses than browser automation.

The login still uses browser automation to handle authentication,
but all subsequent operations use direct API calls.
"""

from __future__ import annotations

import json
from typing import Generator, Literal

import modal
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.core.config import load_config

# Load configuration
_config = load_config()

# Modal app name for looking up deployed functions
MODAL_APP_NAME = _config.app.name

# Track login state
_logged_in = False


API_SYSTEM_PROMPT = """You are a helpful grocery shopping assistant for Real Canadian Superstore.

## Your Capabilities (API-Based - Very Fast!)
You have access to fast API-based tools that don't require browser automation:

1. **search_products**: Search for grocery items by name
2. **add_to_cart**: Add a specific product to cart using its product code
3. **view_cart**: View current cart contents

## CRITICAL WORKFLOW:
1. When user wants to add items, ALWAYS search first to get the product CODE
2. Product codes look like "20657896_EA" or "20132621001_KG"
3. Present search results to user and let them choose which product
4. Use the exact code from search results when adding to cart

## Example Flow:
User: "Add milk to my cart"
1. Call search_products("milk") to find available products
2. Present options with names, brands, prices
3. Ask user which one they want
4. When user confirms, call add_to_cart with the specific product code

## Guidelines:
- Always search before adding to confirm products exist
- Show search results so users can pick the right product
- Use view_cart to confirm items were added
- Be concise but helpful
- If a product isn't found, suggest alternative search terms

## Important Notes:
- Product codes have suffixes: _EA (each), _KG (by weight)
- Prices are current for the selected store
- Login is automatic when needed
"""


def get_modal_function(function_name: str) -> modal.Function:
    """Look up a deployed Modal function."""
    return modal.Function.from_name(MODAL_APP_NAME, function_name)


def _ensure_logged_in_streaming() -> Generator[dict, None, tuple[bool, str]]:
    """Streaming login that yields progress events."""
    global _logged_in

    if _logged_in:
        yield {"type": "login_complete", "status": "success", "message": "Already logged in", "steps": 0}
        return True, "Already logged in."

    print("\n[API Agent] Logging in to Superstore...")

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
                    return True, "Login successful."
                else:
                    return False, f"Login failed: {message}"

        return False, "Login stream ended unexpectedly"

    except modal.exception.NotFoundError:
        error_msg = f"Modal app '{MODAL_APP_NAME}' not found. Deploy with: modal deploy modal/app.py"
        yield {"type": "login_complete", "status": "failed", "message": error_msg, "steps": 0}
        return False, error_msg
    except Exception as e:
        error_msg = f"Login error: {str(e)}"
        yield {"type": "login_complete", "status": "failed", "message": error_msg, "steps": 0}
        return False, error_msg


@tool
def search_products(query: str, max_results: int = 5) -> str:
    """Search for grocery products at Real Canadian Superstore.

    Returns a list of products with their codes, names, brands, and prices.
    Use the product CODE when adding items to cart.

    Args:
        query: Search term (e.g., "milk", "eggs", "chicken breast")
        max_results: Maximum number of results (default 5)

    Returns:
        Formatted list of products
    """
    writer = get_stream_writer()

    try:
        if writer:
            writer({"progress": {"type": "api_search_start", "query": query}})

        search_fn = get_modal_function("api_search_products")
        result = search_fn.remote(query, max_results)

        if writer:
            writer({"progress": {"type": "api_search_complete", "count": result.get("count", 0)}})

        if result.get("status") != "success":
            return f"Error searching: {result.get('message', 'Unknown error')}"

        products = result.get("products", [])
        if not products:
            return f"No products found for '{query}'. Try a different search term."

        output = f"Found {len(products)} products for '{query}':\n\n"

        for i, p in enumerate(products, 1):
            output += f"**{i}. {p['name']}**"
            if p.get("brand"):
                output += f" ({p['brand']})"
            output += "\n"
            output += f"   Code: `{p['code']}`\n"
            output += f"   Price: ${p['price']:.2f}/{p.get('unit', 'ea')}\n"
            if p.get("description"):
                output += f"   {p['description']}\n"
            output += "\n"

        output += "---\n"
        output += "**To add an item, tell me which one you want** "
        output += "(e.g., 'add the first one' or 'add 2 of the Beatrice milk')"

        return output

    except modal.exception.NotFoundError:
        return f"Error: Modal app '{MODAL_APP_NAME}' not found. Deploy with: modal deploy modal/app.py"
    except Exception as e:
        return f"Error searching: {str(e)}"


@tool
def add_to_cart(product_code: str, quantity: int = 1) -> str:
    """Add a product to the shopping cart using its product code.

    IMPORTANT: Search for products first to get the product code!
    Product codes look like: "20657896_EA" or "20132621001_KG"

    Args:
        product_code: The exact product code from search results
        quantity: Number of items to add (default 1)

    Returns:
        Confirmation message
    """
    writer = get_stream_writer()

    # Validate product code format
    if not product_code or "_" not in product_code:
        return (
            f"Invalid product code: '{product_code}'. "
            "Product codes should look like '20657896_EA'. "
            "Please search for the product first."
        )

    try:
        if writer:
            writer({"progress": {"type": "api_add_start", "code": product_code, "quantity": quantity}})

        add_fn = get_modal_function("api_add_to_cart")
        result = add_fn.remote(product_code, quantity)

        if writer:
            writer({"progress": {"type": "api_add_complete", "status": result.get("status")}})

        if result.get("status") == "success":
            return f"Successfully added {quantity}x {product_code} to cart!"
        else:
            return f"Failed to add to cart: {result.get('message', 'Unknown error')}"

    except modal.exception.NotFoundError:
        return f"Error: Modal app '{MODAL_APP_NAME}' not found. Deploy with: modal deploy modal/app.py"
    except Exception as e:
        return f"Error adding to cart: {str(e)}"


@tool
def view_cart() -> str:
    """View the current shopping cart contents.

    Returns:
        List of items in cart with quantities and prices
    """
    writer = get_stream_writer()

    try:
        if writer:
            writer({"progress": {"type": "api_view_cart_start"}})

        view_fn = get_modal_function("api_view_cart")
        result = view_fn.remote()

        if writer:
            writer({"progress": {"type": "api_view_cart_complete", "count": result.get("item_count", 0)}})

        if result.get("status") != "success":
            return f"Error viewing cart: {result.get('message', 'Unknown error')}"

        items = result.get("items", [])
        if not items:
            return "Your cart is empty."

        output = "**Current Cart Contents:**\n\n"

        for item in items:
            name = item.get("name", "Unknown")
            if item.get("brand"):
                name = f"{item['brand']} - {name}"

            qty = item.get("quantity", 1)
            unit_price = item.get("unit_price", 0)
            total = item.get("total_price", 0)

            output += f"- **{qty}x** {name}\n"
            output += f"  ${unit_price:.2f} each = **${total:.2f}**\n\n"

        output += "---\n"
        output += f"**Estimated Total: ${result.get('total', 0):.2f}**"

        return output

    except modal.exception.NotFoundError:
        return f"Error: Modal app '{MODAL_APP_NAME}' not found. Deploy with: modal deploy modal/app.py"
    except Exception as e:
        return f"Error viewing cart: {str(e)}"


# API-based tools for the agent
API_MODAL_TOOLS = [search_products, add_to_cart, view_cart]


class GroceryState(MessagesState):
    """State for the grocery shopping agent."""
    pass


def create_api_modal_agent():
    """Create the API-based grocery shopping chat agent for Modal.

    This agent uses fast API calls for search and cart operations,
    falling back to browser automation only for login.
    """
    config = load_config()

    llm = ChatGroq(
        model=config.llm.chat_model,
        temperature=config.llm.chat_temperature,
    )
    llm_with_tools = llm.bind_tools(API_MODAL_TOOLS)

    def chat_node(state: GroceryState):
        messages = state["messages"]

        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=API_SYSTEM_PROMPT)] + list(messages)

        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: GroceryState) -> Literal["tools", "__end__"]:
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return "__end__"

    workflow = StateGraph(GroceryState)
    workflow.add_node("chat", chat_node)
    workflow.add_node("tools", ToolNode(API_MODAL_TOOLS))
    workflow.add_edge(START, "chat")
    workflow.add_conditional_edges("chat", should_continue)
    workflow.add_edge("tools", "chat")

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)
