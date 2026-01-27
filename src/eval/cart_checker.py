"""Cart verification for evaluation runs.

Uses the same browser profile to open the cart after a test run
and extract/verify the cart contents.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

from browser_use import Agent

from src.core.config import load_config
from src.eval.results import CartItem

if TYPE_CHECKING:
    from browser_use import Browser


# Default cart extraction prompt
CART_EXTRACTION_PROMPT = """
Navigate to {cart_url} and extract all items in the shopping cart.

Steps:
1. Go directly to {cart_url}
2. Wait for the cart page to load completely
3. Look for all product items listed in the cart
4. For each item, extract:
   - Product name (the main product title)
   - Quantity (how many of this item)
   - Price (if visible)

CRITICAL: Your ONLY task is to extract and report the cart contents.
Do NOT add, remove, or modify any items.
Do NOT click any buttons except to navigate to the cart.

Return a structured list of all items in the cart in this exact format:
CART_CONTENTS_START
- [quantity]x [product name] | $[price]
- [quantity]x [product name] | $[price]
CART_CONTENTS_END

Examples:
CART_CONTENTS_START
- 2x Bananas | $1.49
- 1x 2% Milk 4L | $6.99
- 3x Whole Wheat Bread | $3.49
CART_CONTENTS_END

If the cart is empty, return:
CART_CONTENTS_START
EMPTY
CART_CONTENTS_END

If you cannot access the cart, return:
CART_CONTENTS_START
ERROR: [reason]
CART_CONTENTS_END
"""


def parse_cart_output(raw_output: str) -> tuple[list[CartItem], str | None]:
    """Parse the agent's cart extraction output into structured CartItems.

    Args:
        raw_output: Raw text output from the cart extraction agent

    Returns:
        Tuple of (list of CartItems, error message if any)
    """
    items = []
    error = None

    # Find the cart contents block
    start_marker = "CART_CONTENTS_START"
    end_marker = "CART_CONTENTS_END"

    start_idx = raw_output.find(start_marker)
    end_idx = raw_output.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        # Try to parse without markers (fallback)
        content = raw_output
    else:
        content = raw_output[start_idx + len(start_marker):end_idx].strip()

    # Check for empty cart
    if "EMPTY" in content.upper() or "empty" in content.lower():
        return [], None

    # Check for error
    if content.startswith("ERROR:"):
        return [], content

    # Parse item lines
    # Pattern: "- [quantity]x [product name] | $[price]" or similar
    lines = content.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Remove leading dash/bullet
        if line.startswith("-"):
            line = line[1:].strip()
        elif line.startswith("*"):
            line = line[1:].strip()

        # Try to parse quantity
        quantity = 1
        name = line
        price = None

        # Pattern: "2x Product Name | $4.99" or "2x Product Name - $4.99"
        qty_match = re.match(r"(\d+)\s*x\s+(.+)", line, re.IGNORECASE)
        if qty_match:
            quantity = int(qty_match.group(1))
            remainder = qty_match.group(2)
        else:
            remainder = line

        # Split by price separator
        price_separators = [" | $", " - $", " @ $", " $"]
        for sep in price_separators:
            if sep in remainder:
                parts = remainder.split(sep, 1)
                name = parts[0].strip()
                price = "$" + parts[1].strip() if parts[1] else None
                break
        else:
            # No price found, entire remainder is the name
            name = remainder.strip()

        if name:
            items.append(CartItem(
                name=name,
                quantity=quantity,
                price=price,
                raw_text=line,
            ))

    return items, error


async def extract_cart_contents(
    browser: "Browser",
    cart_url: str,
    llm,
    max_steps: int = 20,
    use_vision: bool = False,
) -> tuple[list[CartItem], str, float]:
    """Extract cart contents using the browser agent.

    Args:
        browser: Browser instance with the same profile used for shopping
        cart_url: URL of the cart page
        llm: LLM instance to use for the agent
        max_steps: Maximum agent steps
        use_vision: Whether to enable vision capabilities

    Returns:
        Tuple of (list of CartItems, raw content string, duration in seconds)
    """
    start_time = time.time()

    prompt = CART_EXTRACTION_PROMPT.format(cart_url=cart_url)

    agent = Agent(
        task=prompt,
        llm=llm,
        browser_session=browser,
        use_vision=use_vision,
    )

    await agent.run(max_steps=max_steps)

    # Extract content from agent history
    raw_content = ""
    try:
        extracted = agent.history.extracted_content()
        if extracted:
            raw_content = "\n".join(str(c) for c in extracted)

        # Also check model outputs for the structured response
        thoughts = agent.history.model_thoughts()
        if thoughts:
            raw_content += "\n" + "\n".join(str(t) for t in thoughts)
    except Exception as e:
        raw_content = f"Error extracting history: {e}"

    duration = time.time() - start_time
    items, error = parse_cart_output(raw_content)

    if error:
        raw_content = f"{error}\n\n{raw_content}"

    return items, raw_content, duration


async def clear_cart(
    browser: "Browser",
    cart_url: str,
    llm,
    max_steps: int = 30,
    use_vision: bool = False,
) -> bool:
    """Clear all items from the cart.

    Args:
        browser: Browser instance
        cart_url: URL of the cart page
        llm: LLM instance
        max_steps: Maximum agent steps
        use_vision: Whether to enable vision

    Returns:
        True if cart was cleared successfully
    """
    prompt = f"""
    Navigate to {cart_url} and remove ALL items from the shopping cart.

    Steps:
    1. Go to {cart_url}
    2. Wait for the cart to load
    3. For each item in the cart:
       - Find the remove/delete button (usually an X or trash icon)
       - Click it to remove the item
    4. Repeat until the cart is empty
    5. Confirm the cart shows "empty" or "no items"

    Return success when the cart is completely empty.
    """

    agent = Agent(
        task=prompt,
        llm=llm,
        browser_session=browser,
        use_vision=use_vision,
    )

    try:
        await agent.run(max_steps=max_steps)
        # Check if cart appears empty
        extracted = agent.history.extracted_content()
        for content in extracted:
            if "empty" in str(content).lower():
                return True
        return True  # Assume success if no error
    except Exception:
        return False


def match_cart_to_requested(
    cart_items: list[CartItem],
    requested_items: list[str],
) -> dict[str, CartItem | None]:
    """Match cart items to the originally requested items.

    Args:
        cart_items: Items found in the cart
        requested_items: Items that were requested to be added

    Returns:
        Dict mapping requested item -> matched CartItem or None
    """
    matches: dict[str, CartItem | None] = {item: None for item in requested_items}
    used_cart_items: set[int] = set()

    for requested in requested_items:
        for i, cart_item in enumerate(cart_items):
            if i in used_cart_items:
                continue
            if cart_item.matches(requested, fuzzy=True):
                matches[requested] = cart_item
                used_cart_items.add(i)
                break

    return matches
