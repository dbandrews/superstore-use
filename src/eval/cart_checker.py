"""Cart verification for evaluation runs.

Uses the Real Canadian Superstore API to extract detailed cart contents
after a test run. Includes an LLM judge for semantic matching of
requested items vs cart contents.

API extraction provides rich product data:
- Full brand names and product names
- Package sizes (calculated from comparison prices)
- Product descriptions
- Pricing information

Fast and reliable (~200-500ms), no LLM required for extraction.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage
from playwright.async_api import Page
from pydantic import BaseModel, Field

from src.eval.results import CartItem

if TYPE_CHECKING:
    from browser_use import Browser


async def extract_cart_contents_api(
    page: Page,
    cart_url: str,
    api_key: str | None = None,
) -> tuple[list[CartItem], str, float]:
    """Extract cart contents via Real Canadian Superstore API.

    Uses the pcexpress.ca BFF API to fetch structured cart data with
    detailed product information (brand, description, package size).

    Args:
        page: Playwright page (browser session with valid cookies)
        cart_url: Cart URL (used to ensure user is on cart page if needed)
        api_key: API key for pcexpress.ca (defaults to known static value)

    Returns:
        Tuple of (cart_items, raw_json, duration_seconds)

    Raises:
        ValueError: If cart ID not found in localStorage
        RuntimeError: If API request fails
    """
    start = time.time()

    # Default API key for Real Canadian Superstore
    if api_key is None:
        api_key = "C1xujSegT5j3ap3yexJjqhOfELwGKYvz"

    # Extract cart ID from localStorage
    cart_id = await page.evaluate("() => localStorage.getItem('ANONYMOUS_CART_ID')")

    if not cart_id:
        raise ValueError(
            "Cart ID not found in localStorage. "
            "User may need to log in or visit cart page first."
        )

    # Fetch cart data via API
    cart_data = await page.evaluate(
        """
        async (args) => {
            const response = await fetch(
                `https://api.pcexpress.ca/pcx-bff/api/v1/carts/${args.cartId}`,
                {
                    headers: {
                        'accept': 'application/json',
                        'x-apikey': args.apiKey,
                        'site-banner': 'superstore',
                        'x-application-type': 'Web'
                    }
                }
            );

            if (!response.ok) {
                throw new Error(`Cart API returned ${response.status}: ${response.statusText}`);
            }

            return await response.json();
        }
        """,
        {"cartId": cart_id, "apiKey": api_key},
    )

    # Extract products from response
    cart_items = []
    for order in cart_data.get("orders", []):
        for entry in order.get("entries", []):
            product = entry["offer"]["product"]
            prices = entry["prices"]

            # Calculate package size from comparison prices
            comp_prices = prices.get("comparisonPrices", [])
            package_info = ""
            if comp_prices:
                comp = comp_prices[0]
                # Calculate size: (product_price / comparison_price) * comparison_quantity
                try:
                    size_value = (product["price"] / comp["price"]) * comp["quantity"]
                    # Round to reasonable precision
                    size_rounded = round(size_value) if size_value >= 10 else round(size_value, 1)
                    package_info = f" ({size_rounded} {comp['unit']})"
                except (ZeroDivisionError, KeyError):
                    pass  # Skip package info if calculation fails

            # Format cart item with rich details
            cart_items.append(
                CartItem(
                    name=f"{product['brand']} - {product['name']}{package_info}",
                    quantity=int(entry["quantity"]),
                    price=f"${product['price']:.2f}",
                    raw_text=product.get("description", ""),  # Full description for judge
                )
            )

    duration = time.time() - start
    raw_json = json.dumps(cart_data, indent=2)

    return cart_items, raw_json, duration


class ItemJudgment(BaseModel):
    """Judgment result for a single requested item."""

    requested_item: str = Field(description="The item that was requested")
    found: bool = Field(description="Whether the item was found in the cart")
    correct_quantity: bool = Field(description="Whether the quantity matches")
    matched_cart_item: str | None = Field(default=None, description="The cart item that matched, if any")
    matched_quantity: int | None = Field(default=None, description="The quantity found in cart")
    requested_quantity: int = Field(default=1, description="The quantity that was requested")
    reasoning: str = Field(default="", description="Explanation of the judgment")


class CartJudgment(BaseModel):
    """Overall judgment of cart contents vs requested items."""

    item_judgments: list[ItemJudgment] = Field(default_factory=list)
    all_items_found: bool = Field(default=False)
    all_quantities_correct: bool = Field(default=False)
    overall_success: bool = Field(default=False)
    summary: str = Field(default="")


LLM_JUDGE_PROMPT = '''You are a shopping cart verification judge. Your task is to compare a list of REQUESTED ITEMS against the ACTUAL CART CONTENTS and determine if each requested item was successfully added with the correct quantity.

## REQUESTED ITEMS:
{requested_items}

## ACTUAL CART CONTENTS:
{cart_contents}

## INSTRUCTIONS:
For each requested item, determine:

1. **Found**: Is there a matching product in the cart? Use semantic matching - the product doesn't need to be an exact string match, but should be the same type of product.
   - "apples" matches "Naturally Imperfect Apples, 6 lb bag" ✓
   - "2% milk" matches "Beatrice 2% Milk, 4L" ✓
   - "apples" does NOT match "Apple Juice" ✗
   - "chicken breast" does NOT match "Chicken Wings" ✗

2. **Correct Quantity**: Does the quantity in the cart match what was requested?
   - If no quantity was specified in the request, assume quantity of 1
   - "3 apples" requires quantity >= 3 in cart
   - "milk" with no quantity requires quantity >= 1

## OUTPUT FORMAT:
Return a JSON object with this exact structure:
```json
{{
  "item_judgments": [
    {{
      "requested_item": "the original requested item string",
      "found": true/false,
      "correct_quantity": true/false,
      "matched_cart_item": "name of matching cart item or null",
      "matched_quantity": number or null,
      "requested_quantity": number (parsed from request, default 1),
      "reasoning": "brief explanation"
    }}
  ],
  "all_items_found": true/false,
  "all_quantities_correct": true/false,
  "overall_success": true/false (all items found AND quantities correct),
  "summary": "one sentence summary"
}}
```

Return ONLY the JSON object, no other text.'''


def _create_judge_llm(llm_config: dict):
    """Create a langchain LLM instance for the judge.

    Uses langchain directly instead of browser-use wrappers for compatibility
    with ainvoke() using message objects.

    Args:
        llm_config: Dict with 'provider', 'model', 'temperature' keys

    Returns:
        Langchain chat model instance
    """
    provider = llm_config.get("provider", "openai")
    model = llm_config.get("model", "gpt-4o")
    temperature = llm_config.get("temperature", 0.0)

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature)
    elif provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, temperature=temperature)
    else:
        # Default to OpenAI
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature)


async def judge_cart_contents(
    requested_items: list[str],
    cart_items: list[CartItem],
    llm_config: dict,
    custom_prompt: str | None = None,
) -> CartJudgment:
    """Use an LLM to judge if cart contents match requested items.

    Args:
        requested_items: List of items that were requested to be added
        cart_items: Items found in the cart
        llm_config: Dict with LLM config (provider, model, temperature)
        custom_prompt: Optional custom prompt template. Must contain {requested_items}
            and {cart_contents} placeholders. If None, uses default LLM_JUDGE_PROMPT.

    Returns:
        CartJudgment with detailed results for each item
    """
    # Format requested items
    requested_str = "\n".join(f"- {item}" for item in requested_items)

    # Format cart contents
    if cart_items:
        cart_lines = []
        for item in cart_items:
            line = f"- {item.quantity}x {item.name}"
            if item.price:
                line += f" | {item.price}"
            cart_lines.append(line)
        cart_str = "\n".join(cart_lines)
    else:
        cart_str = "(Cart is empty)"

    # Build the prompt using custom template or default
    prompt_template = custom_prompt if custom_prompt else LLM_JUDGE_PROMPT
    prompt = prompt_template.format(
        requested_items=requested_str,
        cart_contents=cart_str,
    )

    # Create a separate langchain LLM for judging (not browser-use wrapper)
    judge_llm = _create_judge_llm(llm_config)

    # Call the LLM
    try:
        response = await judge_llm.ainvoke([HumanMessage(content=prompt)])
        response_text = response.content if hasattr(response, 'content') else str(response)

        # Extract JSON from response
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            judgment_data = json.loads(json_match.group())
            return CartJudgment.model_validate(judgment_data)
        else:
            return CartJudgment(
                summary=f"Failed to parse LLM response: {response_text[:200]}"
            )

    except Exception as e:
        return CartJudgment(
            summary=f"LLM judge error: {str(e)}"
        )


async def extract_cart_contents(
    browser: "Browser",
    cart_url: str,
    api_key: str | None = None,
) -> tuple[list[CartItem], str, float]:
    """Extract cart contents using API only.

    Uses the Real Canadian Superstore API to fetch detailed cart data
    including product names, brands, descriptions, and package sizes.

    Args:
        browser: Browser instance with the same profile used for shopping
        cart_url: URL of the cart page
        api_key: Optional API key override (defaults to known static key)

    Returns:
        Tuple of (list of CartItems, raw JSON string, duration in seconds)

    Raises:
        ValueError: If cart ID not found in localStorage
        RuntimeError: If API request fails
    """
    # Start browser if not already started
    # browser-use requires explicit start() when not using Agent
    try:
        await browser.start()
    except Exception:
        # Already started or initialization error - continue
        pass

    # Create a new page without URL first
    page = await browser.new_page()

    # Navigate to cart URL and wait for full page load
    await page.goto(cart_url)

    # Wait for network to be idle to ensure localStorage is accessible
    await asyncio.sleep(2)

    # Extract via API (only method)
    return await extract_cart_contents_api(
        page=page,
        cart_url=cart_url,
        api_key=api_key,
    )


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
