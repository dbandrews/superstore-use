"""API-based tools for the grocery shopping agent.

These tools use the Superstore API directly instead of browser automation,
providing faster and more reliable operations (~200-500ms vs 30-60 seconds).

Usage:
    1. Call set_credentials() after login to initialize the API client
    2. Use the tools via the agent or directly

Tools:
    - search_products: Search for grocery items
    - add_item_to_cart: Add a product by code
    - view_cart: View current cart contents
"""

from __future__ import annotations

from langchain_core.tools import tool

from src.api.client import SuperstoreAPIClient
from src.api.credentials import SuperstoreCredentials


# Global client instance (initialized after login or cart creation)
_client: SuperstoreAPIClient | None = None
_credentials: SuperstoreCredentials | None = None


def set_credentials(credentials: SuperstoreCredentials) -> None:
    """Set credentials and initialize the API client.

    Call this after login to enable API-based tools.

    Args:
        credentials: Credentials extracted from browser session
    """
    global _client, _credentials
    _credentials = credentials
    _client = SuperstoreAPIClient(credentials)


def get_client() -> SuperstoreAPIClient:
    """Get the API client, raising if not initialized.

    Returns:
        The initialized SuperstoreAPIClient

    Raises:
        RuntimeError: If credentials haven't been set
    """
    if _client is None:
        raise RuntimeError(
            "API client not initialized. Call set_credentials() first, "
            "or use initialize_anonymous_session() for testing."
        )
    return _client


def get_credentials() -> SuperstoreCredentials | None:
    """Get the current credentials."""
    return _credentials


async def initialize_anonymous_session(store_id: str = "1545") -> SuperstoreCredentials:
    """Initialize an anonymous shopping session.

    Creates a new cart without requiring browser login.
    Useful for testing or guest shopping.

    Args:
        store_id: Store ID for pricing/availability

    Returns:
        Credentials with new cart ID
    """
    global _client, _credentials

    _credentials = SuperstoreCredentials(store_id=store_id)
    _client = SuperstoreAPIClient(_credentials)

    # Create a new cart
    await _client.create_cart()

    return _credentials


@tool
async def search_products(query: str, max_results: int = 5) -> str:
    """Search for grocery products at Real Canadian Superstore.

    Returns a list of matching products with their codes, names, brands,
    and prices. Use the product CODE when adding items to cart.

    Args:
        query: Search term (e.g., "milk", "eggs", "chicken breast")
        max_results: Maximum number of results to return (default 5)

    Returns:
        Formatted list of products with codes for adding to cart
    """
    client = get_client()

    try:
        results = await client.search(query, size=max_results)

        if not results:
            return f"No products found for '{query}'. Try a different search term."

        output = f"Found {len(results)} products for '{query}':\n\n"

        for i, product in enumerate(results, 1):
            output += f"**{i}. {product.name}**"
            if product.brand:
                output += f" ({product.brand})"
            output += "\n"
            output += f"   Code: `{product.code}`\n"
            output += f"   Price: ${product.price:.2f}/{product.unit}\n"

            # Truncate long descriptions
            if product.description:
                desc = product.description[:80]
                if len(product.description) > 80:
                    desc += "..."
                output += f"   {desc}\n"
            output += "\n"

        output += "---\n"
        output += "**To add an item to your cart, tell me which one you want** "
        output += "(e.g., 'add the first one' or 'add 2 of the Beatrice milk')"

        return output

    except Exception as e:
        return f"Error searching for products: {e}"


@tool
async def add_item_to_cart(product_code: str, quantity: int = 1) -> str:
    """Add a product to the shopping cart using its product code.

    IMPORTANT: You must search for products first to get the product code!
    Product codes look like: "20657896_EA" or "20132621001_KG"

    Args:
        product_code: The exact product code from search results
        quantity: Number of items to add (default 1)

    Returns:
        Confirmation message with cart status
    """
    client = get_client()

    try:
        # Validate the product code format
        if not product_code or "_" not in product_code:
            return (
                f"Invalid product code: '{product_code}'. "
                "Product codes should look like '20657896_EA'. "
                "Please search for the product first to get the correct code."
            )

        await client.add_to_cart({product_code: quantity})

        return f"Successfully added {quantity}x {product_code} to cart!"

    except Exception as e:
        error_msg = str(e)
        if "404" in error_msg:
            return (
                f"Product '{product_code}' not found. "
                "The product may be unavailable. Try searching again."
            )
        return f"Error adding {product_code} to cart: {e}"


@tool
async def view_cart() -> str:
    """View the current shopping cart contents.

    Returns a list of all items in the cart with quantities, prices,
    and the estimated total.

    Returns:
        Formatted cart contents with totals
    """
    client = get_client()

    try:
        entries = await client.get_cart()

        if not entries:
            return "Your cart is empty."

        output = "**Current Cart Contents:**\n\n"
        total = 0.0

        for entry in entries:
            line_total = entry.total_price
            total += line_total

            display_name = entry.display_name()
            output += f"- **{entry.quantity}x** {display_name}\n"
            output += f"  ${entry.unit_price:.2f} each = **${line_total:.2f}**\n\n"

        output += "---\n"
        output += f"**Estimated Total: ${total:.2f}**"

        return output

    except Exception as e:
        return f"Error viewing cart: {e}"


# Export tools for use in agent
API_TOOLS = [search_products, add_item_to_cart, view_cart]
