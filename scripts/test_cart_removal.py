"""Test script to verify PC Express cart item removal via the BFF API.

Hypothesis: Sending POST /carts/{cart_id} with quantity=0 for a product code
removes that item from the cart (based on SAP Hybris CartService behavior).

Usage:
    uv run python scripts/test_cart_removal.py
"""

import asyncio
import json
import sys

import httpx

PCX_BASE = "https://api.pcexpress.ca/pcx-bff/api/v1"
PCX_BASE_HEADERS = {
    "x-apikey": "C1xujSegT5j3ap3yexJjqhOfELwGKYvz",
    "x-loblaw-tenant-id": "ONLINE_GROCERIES",
    "x-channel": "web",
    "x-application-type": "web",
    "business-user-agent": "PCXWEB",
    "accept-language": "en",
    "content-type": "application/json",
}

BANNER = "superstore"


def pcx_headers(banner: str = BANNER) -> dict:
    return {**PCX_BASE_HEADERS, "basesiteid": banner, "site-banner": banner}


def log_step(step: str, msg: str):
    print(f"\n{'='*60}")
    print(f"[{step}] {msg}")
    print(f"{'='*60}")


def log_response(label: str, resp: httpx.Response, data: dict):
    print(f"  HTTP {resp.status_code}")
    print(f"  Response ({label}):")
    # Truncate large responses
    text = json.dumps(data, indent=2)
    if len(text) > 3000:
        print(f"  {text[:3000]}...")
        print(f"  ... (truncated, {len(text)} chars total)")
    else:
        print(f"  {text}")


def get_cart_entries(cart_data: dict) -> list[dict]:
    """Extract entries from a cart response."""
    entries = []
    cart_obj = cart_data.get("cart", cart_data)
    for order in cart_obj.get("orders", []):
        for entry in order.get("entries", []):
            product = entry.get("offer", {}).get("product", {})
            entries.append({
                "code": product.get("code") or product.get("id"),
                "name": product.get("name"),
                "quantity": entry.get("quantity"),
            })
    return entries


async def main():
    async with httpx.AsyncClient(timeout=30) as client:

        # Step 1: Find a store
        log_step("1", "Finding a Real Canadian Superstore location...")
        resp = await client.get(
            f"{PCX_BASE}/pickup-locations",
            headers=pcx_headers(),
            params={"bannerIds": BANNER},
        )
        locations = resp.json()
        if not locations:
            print("  ERROR: No locations found")
            sys.exit(1)
        store = locations[0]
        store_id = store.get("storeId")
        store_name = store.get("name")
        print(f"  Using store: {store_name} (ID: {store_id})")

        # Step 2: Create a cart
        log_step("2", f"Creating cart for store {store_id}...")
        resp = await client.post(
            f"{PCX_BASE}/carts",
            headers=pcx_headers(),
            json={"bannerId": BANNER, "language": "en", "storeId": store_id},
        )
        data = resp.json()
        log_response("create-cart", resp, data)
        cart_id = data.get("cartId") or data.get("id")
        if not cart_id:
            print("  ERROR: No cart_id returned")
            sys.exit(1)
        print(f"  Cart ID: {cart_id}")

        # Step 3: Search for a product
        log_step("3", 'Searching for "banana"...')
        resp = await client.post(
            f"{PCX_BASE}/products/search",
            headers=pcx_headers(),
            json={
                "term": "banana",
                "banner": BANNER,
                "storeId": store_id,
                "lang": "en",
                "cartId": cart_id,
                "pagination": {"from": 0, "size": 5},
            },
        )
        data = resp.json()
        results = data.get("results", [])
        # Pick first shoppable, in-stock result
        product = None
        for r in results:
            if r.get("shoppable", True) and r.get("stockStatus") == "OK":
                product = r
                break
        if not product:
            print("  ERROR: No shoppable product found")
            print(f"  Results: {json.dumps(results[:3], indent=2)}")
            sys.exit(1)
        product_code = product["code"]
        product_name = product.get("name", "?")
        print(f"  Found: {product_code} - {product_name}")

        # Step 4: Add it to the cart
        log_step("4", f"Adding {product_code} to cart with quantity=1...")
        entries = {
            product_code: {
                "quantity": 1,
                "fulfillmentMethod": "pickup",
                "sellerId": store_id,
            }
        }
        resp = await client.post(
            f"{PCX_BASE}/carts/{cart_id}",
            headers=pcx_headers(),
            json={"entries": entries},
        )
        data = resp.json()
        log_response("add-to-cart", resp, data)

        # Step 5: Verify item is in the cart
        log_step("5", "Verifying item is in cart (GET /carts/{cart_id})...")
        resp = await client.get(
            f"{PCX_BASE}/carts/{cart_id}",
            headers=pcx_headers(),
        )
        data = resp.json()
        log_response("get-cart-after-add", resp, data)
        entries_in_cart = get_cart_entries(data)
        print(f"\n  Cart entries after ADD: {json.dumps(entries_in_cart, indent=2)}")
        found = any(e["code"] == product_code for e in entries_in_cart)
        if not found:
            print(f"  WARNING: Product {product_code} NOT found in cart after adding!")
        else:
            print(f"  CONFIRMED: Product {product_code} is in the cart")

        # Step 6: Attempt removal with quantity=0
        log_step("6", f"Attempting removal: POST /carts/{cart_id} with quantity=0...")
        removal_entries = {
            product_code: {
                "quantity": 0,
                "fulfillmentMethod": "pickup",
                "sellerId": store_id,
            }
        }
        resp = await client.post(
            f"{PCX_BASE}/carts/{cart_id}",
            headers=pcx_headers(),
            json={"entries": removal_entries},
        )
        data = resp.json()
        log_response("remove-attempt-qty-0", resp, data)
        errors = data.get("errors", [])
        if errors:
            print(f"\n  API errors: {json.dumps(errors, indent=2)}")

        # Step 7: Verify item is gone
        log_step("7", "Verifying item is removed (GET /carts/{cart_id})...")
        resp = await client.get(
            f"{PCX_BASE}/carts/{cart_id}",
            headers=pcx_headers(),
        )
        data = resp.json()
        log_response("get-cart-after-remove", resp, data)
        entries_after = get_cart_entries(data)
        print(f"\n  Cart entries after REMOVE: {json.dumps(entries_after, indent=2)}")
        still_found = any(e["code"] == product_code for e in entries_after)

        # Summary
        log_step("RESULT", "Summary")
        if not still_found and found:
            print("  SUCCESS: quantity=0 REMOVED the item from the cart!")
            print("  The hypothesis is confirmed.")
        elif still_found:
            print("  FAILED: Item is still in the cart after quantity=0.")
            print("  The quantity=0 approach does NOT work for this BFF.")
            print("  Need to investigate alternative approaches (DELETE endpoint, etc.)")
        else:
            print("  INCONCLUSIVE: Item was never confirmed in cart.")


if __name__ == "__main__":
    asyncio.run(main())
