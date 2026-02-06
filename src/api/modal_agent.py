"""API-based Chat Agent for Modal deployment.

This agent uses the Modal-deployed API functions for product search
and cart operations, providing much faster responses than browser automation.

The login still uses browser automation to handle authentication,
but all subsequent operations use direct API calls.
"""

from __future__ import annotations

import os
from typing import Literal

from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from src.core.config import load_config
from src.api.client import SuperstoreAPIClient
from src.api.credentials import SuperstoreCredentials

# Load configuration
_config = load_config()

# Shared API client instance to persist cart across tool calls
_shared_client: SuperstoreAPIClient | None = None
_shared_credentials: SuperstoreCredentials | None = None
_is_authenticated: bool = False


def _load_stored_credentials() -> tuple[SuperstoreCredentials | None, bool]:
    """Load credentials from Modal Dict or Volume.

    Returns:
        Tuple of (credentials, is_authenticated)
    """
    import json
    from pathlib import Path

    # Try Modal Dict first (fast, in-memory)
    try:
        import modal
        api_dict = modal.Dict.from_name("superstore-api-credentials")
        creds_dict = api_dict.get("default", None)
        if creds_dict:
            print(f"[API Agent] Found credentials in Modal Dict: keys={list(creds_dict.keys())}")
            is_auth = bool(creds_dict.get("bearer_token"))
            print(f"[API Agent] Authenticated: {is_auth}, cart_id: {creds_dict.get('cart_id', 'None')[:8] if creds_dict.get('cart_id') else 'None'}...")
            return SuperstoreCredentials.from_dict(creds_dict), is_auth
    except Exception as e:
        print(f"[API Agent] Could not read Modal Dict: {e}")

    # Fall back to Volume (persistent storage)
    try:
        creds_file = Path("/session/api_credentials.json")
        if creds_file.exists():
            creds_dict = json.loads(creds_file.read_text())
            print(f"[API Agent] Found credentials in Volume: keys={list(creds_dict.keys())}")
            is_auth = bool(creds_dict.get("bearer_token"))
            print(f"[API Agent] Authenticated: {is_auth}, cart_id: {creds_dict.get('cart_id', 'None')[:8] if creds_dict.get('cart_id') else 'None'}...")
            return SuperstoreCredentials.from_dict(creds_dict), is_auth
        else:
            print("[API Agent] No credentials file in Volume")
    except Exception as e:
        print(f"[API Agent] Could not read Volume: {e}")

    return None, False


async def get_shared_client() -> SuperstoreAPIClient:
    """Get or create a shared API client that persists the cart."""
    global _shared_client, _shared_credentials, _is_authenticated

    if _shared_client is None or _shared_credentials is None:
        # Try to load stored credentials first
        stored_creds, is_auth = _load_stored_credentials()

        if stored_creds and stored_creds.cart_id:
            _shared_credentials = stored_creds
            _is_authenticated = is_auth
            print(f"[API Agent] Using stored credentials (authenticated={is_auth})")
        else:
            # Fall back to anonymous credentials
            _shared_credentials = SuperstoreCredentials()
            _is_authenticated = False
            print("[API Agent] No stored credentials, using anonymous session")

        _shared_client = SuperstoreAPIClient(_shared_credentials)

        # Ensure cart exists (will create if needed)
        await _shared_client.ensure_cart()
        print(f"[API Agent] Cart ready: {_shared_credentials.cart_id}")

    return _shared_client


def is_authenticated() -> bool:
    """Check if the current session is authenticated."""
    return _is_authenticated


API_SYSTEM_PROMPT = """You are a helpful grocery shopping assistant for Real Canadian Superstore.

## Your Capabilities (API-Based - Very Fast!)
You have access to fast API-based tools that don't require browser automation:

1. **search_products**: Search for grocery items by name
2. **add_to_cart**: Add a specific product to cart using its product code
3. **view_cart**: View current cart contents
4. **check_login_status**: Check if the user is logged in

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

## Login Status:
- If user asks about login status, use check_login_status tool
- Anonymous users can still search and add to cart (cart is session-based)
- Authenticated users have their cart linked to their account for checkout
- If user says "login", tell them to use the browser-based login flow
"""


@tool
async def search_products(query: str, max_results: int = 5) -> str:
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

        # Use shared client to persist cart across calls
        client = await get_shared_client()
        results = await client.search(query, size=max_results)

        if writer:
            writer({"progress": {"type": "api_search_complete", "count": len(results)}})

        if not results:
            return f"No products found for '{query}'. Try a different search term."

        output = f"Found {len(results)} products for '{query}':\n\n"

        for i, p in enumerate(results, 1):
            output += f"**{i}. {p.name}**"
            if p.brand:
                output += f" ({p.brand})"
            output += "\n"
            output += f"   Code: `{p.code}`\n"
            output += f"   Price: ${p.price:.2f}/{p.unit}\n"
            if p.description:
                desc = p.description[:80] + "..." if len(p.description) > 80 else p.description
                output += f"   {desc}\n"
            output += "\n"

        output += "---\n"
        output += "**To add an item, tell me which one you want** "
        output += "(e.g., 'add the first one' or 'add 2 of the Beatrice milk')"

        return output

    except Exception as e:
        return f"Error searching: {str(e)}"


@tool
async def add_to_cart(product_code: str, quantity: int = 1) -> str:
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

        # Use shared client to persist cart across calls
        client = await get_shared_client()
        await client.add_to_cart({product_code: quantity})

        if writer:
            writer({"progress": {"type": "api_add_complete", "status": "success"}})

        return f"Successfully added {quantity}x {product_code} to cart!"

    except Exception as e:
        return f"Error adding to cart: {str(e)}"


@tool
async def view_cart() -> str:
    """View the current shopping cart contents.

    Returns:
        List of items in cart with quantities and prices
    """
    writer = get_stream_writer()

    try:
        if writer:
            writer({"progress": {"type": "api_view_cart_start"}})

        # Use shared client to persist cart across calls
        client = await get_shared_client()
        entries = await client.get_cart()

        if writer:
            writer({"progress": {"type": "api_view_cart_complete", "count": len(entries)}})

        if not entries:
            return "Your cart is empty."

        output = "**Current Cart Contents:**\n\n"
        total = 0.0

        for entry in entries:
            name = entry.name
            if entry.brand:
                name = f"{entry.brand} - {name}"

            output += f"- **{entry.quantity}x** {name}\n"
            output += f"  ${entry.unit_price:.2f} each = **${entry.total_price:.2f}**\n\n"
            total += entry.total_price

        output += "---\n"
        output += f"**Estimated Total: ${total:.2f}**"

        return output

    except Exception as e:
        return f"Error viewing cart: {str(e)}"


@tool
async def check_login_status() -> str:
    """Check if the current session is authenticated with a logged-in account.

    Returns:
        Status message indicating if the user is logged in or using anonymous session
    """
    # Ensure client is initialized to check credentials
    await get_shared_client()

    if is_authenticated():
        return (
            "You are logged in with an authenticated account. "
            "Your cart is linked to your Real Canadian Superstore account and will be saved."
        )
    else:
        return (
            "You are using an anonymous session (not logged in). "
            "You can still search and add items to cart, but the cart is session-based only. "
            "To link your cart to your account for checkout, you would need to log in through the browser-based login flow."
        )


# API-based tools for the agent
API_MODAL_TOOLS = [search_products, add_to_cart, view_cart, check_login_status]


class GroceryState(MessagesState):
    """State for the grocery shopping agent."""
    pass


def create_api_modal_agent():
    """Create the API-based grocery shopping chat agent for Modal.

    This agent uses fast API calls for search and cart operations,
    falling back to browser automation only for login.

    Uses OpenAI GPT-4o-mini for reliable tool calling support.
    """
    config = load_config()

    # Use OpenAI for reliable tool calling (Groq models have issues with tool format)
    llm = ChatOpenAI(
        model=os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
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
