"""Success detection utilities for browser automation.

Provides shared success detection logic for determining if items were
successfully added to cart.
"""

# Success indicators for detecting if item was added to cart
SUCCESS_INDICATORS = [
    "added to cart",
    "add to cart",
    "item added",
    "cart updated",
    "in your cart",
    "added to your cart",
    "quantity updated",
]


def detect_success_from_history(agent) -> tuple[bool, str | None]:
    """Parse browser-use agent history to detect if item was added successfully.

    Checks:
    1. Extracted content for success indicator phrases
    2. Model thoughts for success indicators
    3. Whether the agent ended on a cart page

    Args:
        agent: browser-use Agent instance with history

    Returns:
        tuple: (success: bool, evidence: str | None)
            - success: True if item appears to have been added
            - evidence: Description of what indicated success, or None
    """
    try:
        # Check extracted content for success indicators
        extracted = agent.history.extracted_content()
        for content in extracted:
            content_lower = str(content).lower()
            for indicator in SUCCESS_INDICATORS:
                if indicator in content_lower:
                    return True, str(content)[:100]

        # Also check model thoughts for success indicators
        thoughts = agent.history.model_thoughts()
        for thought in thoughts:
            thought_lower = str(thought).lower()
            if any(ind in thought_lower for ind in SUCCESS_INDICATORS):
                return True, str(thought)[:100]

        # Check if we ended up on cart page
        urls = agent.history.urls()
        if urls and "cart" in urls[-1].lower():
            return True, f"Ended on cart page: {urls[-1]}"

    except Exception as e:
        print(f"[Success Detection] Error: {e}")

    return False, None
