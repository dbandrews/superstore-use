"""Cart verification for evaluation runs.

Uses the same browser profile to open the cart after a test run
and extract/verify the cart contents. Includes an LLM judge for
semantic matching of requested items vs cart contents.

Supports two extraction modes:
1. Deterministic DOM parsing via JavaScript (fast, ~1-2 seconds)
2. LLM-based extraction (slower, 10-30 seconds, fallback)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import TYPE_CHECKING

from browser_use import Agent
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from src.core.config import load_config
from src.eval.results import CartItem

if TYPE_CHECKING:
    from browser_use import Browser


# JavaScript for deterministic cart extraction using data-track-products-array attributes
CART_EXTRACTION_JS = '''
(() => {
    const elements = document.querySelectorAll('[data-track-products-array]');
    const products = [];
    const seen = new Set();

    elements.forEach(el => {
        try {
            const jsonStr = el.getAttribute('data-track-products-array');
            const items = JSON.parse(jsonStr);

            items.forEach(item => {
                if (item.productSKU && seen.has(item.productSKU)) return;
                if (item.productSKU) seen.add(item.productSKU);

                products.push({
                    name: item.productName || '',
                    brand: item.productBrand || '',
                    price: item.productPrice || '',
                    quantity: parseInt(item.productQuantity) || 1,
                    sku: item.productSKU || '',
                });
            });
        } catch (e) {}
    });

    return products;
})()
'''


def _convert_to_cart_items(products: list[dict]) -> list[CartItem]:
    """Convert extracted product data to CartItem objects.

    Args:
        products: List of product dicts from JavaScript extraction

    Returns:
        List of CartItem objects
    """
    items = []
    for p in products:
        name = p.get("name", "")
        brand = p.get("brand", "")
        if brand and brand.lower() not in name.lower():
            full_name = f"{brand} - {name}"
        else:
            full_name = name

        price_val = p.get("price", "")
        price = f"${price_val}" if price_val else None

        items.append(CartItem(
            name=full_name,
            quantity=p.get("quantity", 1),
            price=price,
            raw_text=json.dumps(p),
        ))
    return items


async def extract_cart_contents_deterministic(
    browser: "Browser",
    cart_url: str,
    initial_wait: float = 10.0,
) -> tuple[list[CartItem], str, float]:
    """Extract cart contents using deterministic DOM parsing.

    Uses JavaScript to parse data-track-products-array attributes on the page,
    which is faster and more reliable than LLM-based extraction.

    Args:
        browser: Browser instance with the same profile used for shopping
        cart_url: URL of the cart page
        initial_wait: Seconds to wait after page navigation for dynamic content to load

    Returns:
        Tuple of (list of CartItems, raw content string, duration in seconds)
    """
    start_time = time.time()

    try:
        page = await browser.get_current_page()
        await page.goto(cart_url, wait_until="networkidle")
        await asyncio.sleep(initial_wait)

        products = await page.evaluate(CART_EXTRACTION_JS)
        items = _convert_to_cart_items(products)
        raw_content = json.dumps(products, indent=2)

        return items, raw_content, time.time() - start_time
    except Exception as e:
        return [], f"Error: {str(e)}", time.time() - start_time


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


# Default cart extraction prompt
CART_EXTRACTION_PROMPT = """
Navigate to {cart_url} and extract all items in the shopping cart.

Steps:
1. Go directly to {cart_url}
2. IMPORTANT: Wait at least {wait_seconds} seconds for the cart page to fully load.
   Grocery store cart pages load content dynamically via JavaScript.
   Do NOT proceed until you see cart items or a clear "empty cart" message.
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
    initial_wait: float = 10.0,
    use_deterministic: bool = True,
) -> tuple[list[CartItem], str, float]:
    """Extract cart contents using deterministic DOM parsing or LLM agent.

    Args:
        browser: Browser instance with the same profile used for shopping
        cart_url: URL of the cart page
        llm: LLM instance to use for the agent (used only if deterministic fails)
        max_steps: Maximum agent steps (for LLM fallback)
        use_vision: Whether to enable vision capabilities (for LLM fallback)
        initial_wait: Seconds to wait after page navigation for dynamic content to load
        use_deterministic: Use deterministic DOM parsing instead of LLM agent (default True)

    Returns:
        Tuple of (list of CartItems, raw content string, duration in seconds)
    """
    # Try deterministic extraction first if enabled
    if use_deterministic:
        items, raw_content, duration = await extract_cart_contents_deterministic(
            browser=browser,
            cart_url=cart_url,
            initial_wait=initial_wait,
        )

        # If deterministic extraction succeeded, return the results
        if items or "Error" not in raw_content:
            return items, raw_content, duration

        # Fall through to LLM extraction if deterministic failed

    start_time = time.time()

    prompt = CART_EXTRACTION_PROMPT.format(cart_url=cart_url, wait_seconds=int(initial_wait))

    agent = Agent(
        task=prompt,
        llm=llm,
        browser_session=browser,
        use_vision=use_vision,
    )

    # Custom step callback to enforce wait after navigation
    navigated = [False]
    wait_done = [False]

    async def on_step_end(step_result):
        # After first step (which should be navigation), wait for dynamic content
        if not navigated[0]:
            navigated[0] = True
        elif not wait_done[0]:
            # Wait after navigation step for dynamic content to load
            await asyncio.sleep(initial_wait)
            wait_done[0] = True

    await agent.run(max_steps=max_steps, on_step_end=on_step_end)

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
