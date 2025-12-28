"""
Modal-aware tools for the LangGraph grocery shopping agent.

These tools wrap Modal's remote functions to provide browser automation
capabilities that run in the cloud.
"""

import modal
from langchain_core.tools import tool

# Track login state for the session
_logged_in = False


def get_modal_function(function_name: str) -> modal.Function:
    """Look up a deployed Modal function."""
    return modal.Function.from_name("superstore-shopping-agent", function_name)


def _ensure_logged_in() -> tuple[bool, str]:
    """Ensure user is logged in. Returns (success, message)."""
    global _logged_in

    if _logged_in:
        return True, "Already logged in."

    print("\n🔐 Logging in to Superstore...")

    try:
        login_fn = get_modal_function("login_remote")
        result = login_fn.remote()

        if result.get("status") == "success":
            _logged_in = True
            print("✅ Login successful!")
            return True, "Login successful."
        else:
            error_msg = result.get("message", "Unknown error")
            print(f"❌ Login failed: {error_msg}")
            return False, f"Login failed: {error_msg}"
    except Exception as e:
        print(f"❌ Login error: {e}")
        return False, f"Login error: {str(e)}"


@tool
def add_items_to_cart(items: list[str]) -> str:
    """
    Add grocery items to the Real Canadian Superstore cart.

    Items are added in parallel using Modal containers for efficiency.
    Automatically handles login if not already logged in.

    Args:
        items: List of grocery items to add (e.g., ["milk", "eggs", "bread"])

    Returns:
        Summary of which items were added successfully and which failed.
    """
    if not items:
        return "No items provided to add to cart."

    # Ensure logged in before adding items
    login_ok, login_msg = _ensure_logged_in()
    if not login_ok:
        return f"Cannot add items: {login_msg}"

    print(f"\n🛒 Adding {len(items)} items to cart using Modal containers...")

    try:
        add_fn = get_modal_function("add_item_remote")

        # Prepare inputs for starmap: [(item, index), ...]
        inputs = [(item, i) for i, item in enumerate(items, 1)]

        results = []
        for result in add_fn.starmap(inputs, order_outputs=False):
            status_icon = "✅" if result["status"] == "success" else "❌"
            print(f"  {status_icon} {result['item']}: {result['message']}")
            results.append(result)

        successes = [r for r in results if r["status"] == "success"]
        failures = [r for r in results if r["status"] == "failed"]

        summary = f"Added {len(successes)}/{len(items)} items to cart."
        if successes:
            success_items = ", ".join(r["item"] for r in successes)
            summary += f"\n\nSuccessfully added: {success_items}"
        if failures:
            failed_items = ", ".join(f"{r['item']} ({r['message'][:50]})" for r in failures)
            summary += f"\n\nFailed to add: {failed_items}"

        return summary

    except modal.exception.NotFoundError as e:
        return (
            "Error: Modal app 'superstore-shopping-agent' not found. "
            "Please deploy it first with: modal deploy modal_app.py"
        )
    except Exception as e:
        return f"Error adding items: {str(e)}"


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


# Export tools for use in the agent
# Note: login is handled automatically by add_items_to_cart
MODAL_TOOLS = [add_items_to_cart, view_cart]
