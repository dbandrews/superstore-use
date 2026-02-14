View the shopping cart contents at Real Canadian Superstore.

Steps:
1. Go to {base_url}
2. Wait 10 seconds for the page to fully load (JavaScript needs time to render)
3. Look for the cart button in the header. There are TWO cart buttons - one shows "0 items in cart" (placeholder before JS loads) and another shows the real item count. NEVER click the "0 items" button. Only click the button with a NON-ZERO item count. If you only see "0 items" after waiting, wait 5 more seconds and check again.
4. Click the cart button with the real item count and wait 10 seconds for the cart review page to load
5. Scroll down the page to load more items, then scroll back to top
6. Extract all product items from the page with a SINGLE extract call. If quantities are not available, assume quantity is 1 for each item. Do NOT retry extraction more than once.
7. Return the result immediately using the done action. Do not try to get a "perfect" extraction - return what you have.

CRITICAL TIMING: This site renders dynamically. You MUST use explicit wait actions (10 seconds minimum) after both the initial page load AND after navigating to the cart page. Do NOT extract immediately after navigation.

Return a bullet point list of all items in this exact format:
- [quantity]x [product name] - $[price]

Examples:
- 2x Bananas - $1.49
- 1x 2% Milk 4L - $6.99
- 3x Whole Wheat Bread - $3.49

If the cart is empty (cart button shows 0 items after 10 seconds), return exactly: "Your cart is empty."
If you see a login prompt or cannot load the page, return exactly: "Unable to access cart - login may be required."
