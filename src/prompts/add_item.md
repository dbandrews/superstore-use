You need to add "{item}" to the shopping cart on Real Canadian Superstore.
Go to {base_url}

UNDERSTANDING THE ITEM REQUEST:
The item "{item}" may include a quantity (e.g., "6 apples", "2 liters milk", "500g chicken breast").
- Extract the product name to search for (e.g., "apples", "milk", "chicken breast")
- Note the quantity requested (e.g., 6, 2 liters, 500g)

Steps:
1. Use the search bar to search for the PRODUCT NAME (not the full quantity string)
   - For "6 apples", search for "apples"
   - For "2 liters milk", search for "milk"
   - For "500g chicken breast", search for "chicken breast"
2. From the search results, select the most relevant item that matches the quantity/size if possible
   - If looking for "2 liters milk", prefer 2L milk containers
   - If looking for "500g chicken", prefer ~500g packages
3. If a specific quantity is requested (like "6 apples"):
   - Look for a quantity selector/input field on the product
   - Adjust the quantity before adding to cart
   - If no quantity selector, you may need to click "Add to Cart" multiple times
4. Click "Add to Cart", ensuring you have the correct quantity.

Return success immediately when you've added the item, don't confirm the item was added.
