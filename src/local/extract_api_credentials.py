"""Extract API credentials from a logged-in browser session.

Opens a browser with the saved profile and extracts the necessary IDs
for making direct API calls to the Real Canadian Superstore API.

Usage:
    uv run -m src.local.extract_api_credentials
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

# The static API key (same for all users)
API_KEY = "C1xujSegT5j3ap3yexJjqhOfELwGKYvz"
API_BASE_URL = "https://api.pcexpress.ca/pcx-bff/api/v1"


async def extract_credentials(profile_dir: str = "./superstore-profile", headed: bool = True):
    """Extract API credentials from browser state.

    Args:
        profile_dir: Path to the browser profile directory
        headed: Whether to show the browser window

    Returns:
        dict with cart_id, store_id, user_id, and other credentials
    """
    profile_path = Path(profile_dir).resolve()

    if not profile_path.exists():
        print(f"Error: Profile directory not found: {profile_path}")
        print("Run 'uv run -m src.local.cli login' first to create a profile.")
        return None

    print(f"Using profile: {profile_path}")

    async with async_playwright() as p:
        # Launch browser with the saved profile
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=not headed,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=LockProfileCookieDatabase",
            ],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        credentials = {
            "api_key": API_KEY,
            "api_base_url": API_BASE_URL,
            "cart_id": None,
            "store_id": None,
            "user_id": None,
            "postal_code": None,
            "order_id": None,
            "fulfillment_type": None,  # "pickup" or "courier"
            "bearer_token": None,  # JWT authorization token for authenticated requests
            "cookies": {},
        }

        # Set up request/response interception to capture API calls
        captured_data = {
            "cart_id": None,
            "store_id": None,
            "order_id": None,
            "user_id": None,
            "bearer_token": None,
        }

        async def handle_request(request):
            """Capture authorization header from outgoing requests."""
            url = request.url
            if "api.pcexpress.ca" in url:
                headers = request.headers
                auth_header = headers.get("authorization", "")
                if auth_header.startswith("bearer ") or auth_header.startswith("Bearer "):
                    # Extract just the token part (remove "bearer " prefix)
                    token = auth_header[7:]  # len("bearer ") == 7
                    if token and len(token) > 100:  # JWT tokens are long
                        captured_data["bearer_token"] = token
                        # Also extract user_id (pcid) from JWT payload
                        try:
                            import base64
                            # JWT format: header.payload.signature
                            payload_b64 = token.split(".")[1]
                            # Add padding if needed
                            payload_b64 += "=" * (4 - len(payload_b64) % 4)
                            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                            if "pcid" in payload:
                                captured_data["user_id"] = payload["pcid"]
                        except Exception:
                            pass

        page.on("request", handle_request)

        async def handle_response(response):
            url = response.url
            if "api.pcexpress.ca" in url:
                # Extract IDs from URL
                import re
                cart_match = re.search(r"/carts/([a-f0-9-]{36})", url)
                if cart_match:
                    captured_data["cart_id"] = cart_match.group(1)

                store_match = re.search(r"storeId=(\d+)", url)
                if store_match:
                    captured_data["store_id"] = store_match.group(1)

                location_match = re.search(r"locationId=(\d+)", url)
                if location_match and not captured_data["store_id"]:
                    captured_data["store_id"] = location_match.group(1)

                # Try to parse response body for more data
                try:
                    if response.status == 200:
                        body = await response.text()
                        if "cartId" in body:
                            cart_match = re.search(r'"cartId":\s*"([a-f0-9-]{36})"', body)
                            if cart_match and not captured_data["cart_id"]:
                                captured_data["cart_id"] = cart_match.group(1)
                        if "storeId" in body:
                            store_match = re.search(r'"storeId":\s*"?(\d+)"?', body)
                            if store_match and not captured_data["store_id"]:
                                captured_data["store_id"] = store_match.group(1)
                        if "orderId" in body:
                            order_match = re.search(r'"orderId":\s*"([a-f0-9-]{36})"', body)
                            if order_match:
                                captured_data["order_id"] = order_match.group(1)
                except Exception:
                    pass

        page.on("response", handle_response)

        print("Navigating to Real Canadian Superstore...")
        await page.goto("https://www.realcanadiansuperstore.ca/", wait_until="domcontentloaded", timeout=60000)

        # Wait for page to fully load and make API requests
        print("Waiting for page to load and API calls...")
        await asyncio.sleep(8)

        # Extract data from localStorage
        print("Extracting credentials from browser state...")

        local_storage = await page.evaluate("""() => {
            const data = {};
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                data[key] = localStorage.getItem(key);
            }
            return data;
        }""")

        # Extract data from sessionStorage
        session_storage = await page.evaluate("""() => {
            const data = {};
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                data[key] = sessionStorage.getItem(key);
            }
            return data;
        }""")

        # Look for cart ID and store ID in storage
        for key, value in {**local_storage, **session_storage}.items():
            key_lower = key.lower()

            # Try to parse JSON values
            try:
                parsed = json.loads(value) if value else None
            except (json.JSONDecodeError, TypeError):
                parsed = None

            if "cart" in key_lower:
                if isinstance(parsed, dict) and "id" in parsed:
                    credentials["cart_id"] = parsed["id"]
                elif isinstance(parsed, str) and len(parsed) == 36:  # UUID format
                    credentials["cart_id"] = parsed

            if "store" in key_lower or "location" in key_lower:
                if isinstance(parsed, dict):
                    if "storeId" in parsed:
                        credentials["store_id"] = parsed["storeId"]
                    elif "id" in parsed:
                        credentials["store_id"] = parsed["id"]
                elif isinstance(parsed, str) and parsed.isdigit():
                    credentials["store_id"] = parsed

            if "postal" in key_lower:
                if isinstance(parsed, str):
                    credentials["postal_code"] = parsed

            if "user" in key_lower or "pcid" in key_lower:
                if isinstance(parsed, dict) and "id" in parsed:
                    credentials["user_id"] = parsed["id"]
                elif isinstance(parsed, str) and len(parsed) == 36:
                    credentials["user_id"] = parsed

        # Try to extract from URL parameters
        current_url = page.url
        if "cartId=" in current_url:
            import re
            match = re.search(r"cartId=([a-f0-9-]+)", current_url)
            if match:
                credentials["cart_id"] = match.group(1)
        if "storeId=" in current_url:
            import re
            match = re.search(r"storeId=(\d+)", current_url)
            if match:
                credentials["store_id"] = match.group(1)

        # Extract cookies
        cookies = await browser.cookies()
        for cookie in cookies:
            if cookie["domain"] in [".pcexpress.ca", ".realcanadiansuperstore.ca", "realcanadiansuperstore.ca"]:
                credentials["cookies"][cookie["name"]] = cookie["value"]

        # Try to get cart ID by making a small navigation that triggers cart fetch
        # The cart ID often appears in network requests
        print("Checking for cart in network requests...")

        # Navigate to trigger cart API call
        cart_data = await page.evaluate("""async () => {
            // Try to find cart data in window objects
            if (window.__NEXT_DATA__) {
                return window.__NEXT_DATA__;
            }
            return null;
        }""")

        if cart_data:
            # Search through Next.js data for cart info
            cart_data_str = json.dumps(cart_data)
            import re

            # Find cart ID pattern (UUID)
            cart_matches = re.findall(r'"cartId":\s*"([a-f0-9-]{36})"', cart_data_str)
            if cart_matches and not credentials["cart_id"]:
                credentials["cart_id"] = cart_matches[0]

            store_matches = re.findall(r'"storeId":\s*"?(\d+)"?', cart_data_str)
            if store_matches and not credentials["store_id"]:
                credentials["store_id"] = store_matches[0]

            order_matches = re.findall(r'"orderId":\s*"([a-f0-9-]{36})"', cart_data_str)
            if order_matches:
                credentials["order_id"] = order_matches[0]

        # If still no cart ID, try clicking on cart icon to trigger API call
        if not credentials["cart_id"]:
            print("Attempting to extract cart ID from cart page...")
            try:
                # Look for cart button and click it
                cart_button = page.locator('button:has-text("cart")').first
                if await cart_button.count() > 0:
                    await cart_button.click()
                    await asyncio.sleep(2)

                    # Check URL for cart ID
                    current_url = page.url
                    import re
                    match = re.search(r"cartId=([a-f0-9-]+)", current_url)
                    if match:
                        credentials["cart_id"] = match.group(1)
            except Exception:
                pass

        # Get user info from account if logged in
        account_info = await page.evaluate("""() => {
            // Look for user data in common storage patterns
            const patterns = ['pcOptimum', 'user', 'account', 'profile', 'auth'];
            for (const pattern of patterns) {
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    if (key.toLowerCase().includes(pattern)) {
                        try {
                            return { key, value: JSON.parse(localStorage.getItem(key)) };
                        } catch {
                            continue;
                        }
                    }
                }
            }
            return null;
        }""")

        if account_info and isinstance(account_info.get("value"), dict):
            val = account_info["value"]
            if "pcid" in val:
                credentials["user_id"] = val["pcid"]
            elif "id" in val:
                credentials["user_id"] = val["id"]

        # Merge captured data from network requests (these are most reliable)
        if captured_data["cart_id"]:
            credentials["cart_id"] = captured_data["cart_id"]
        if captured_data["store_id"]:
            credentials["store_id"] = captured_data["store_id"]
        if captured_data["order_id"]:
            credentials["order_id"] = captured_data["order_id"]
        if captured_data["bearer_token"]:
            credentials["bearer_token"] = captured_data["bearer_token"]
        if captured_data["user_id"]:
            credentials["user_id"] = captured_data["user_id"]

        # If we have a cart ID, fetch the cart to get the authoritative store ID
        if credentials["cart_id"]:
            print("Fetching cart details for store ID...")
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{API_BASE_URL}/carts/{credentials['cart_id']}",
                        headers={"x-apikey": API_KEY}
                    ) as resp:
                        if resp.status == 200:
                            cart_data = await resp.json()
                            # Extract store ID from fulfillment (works for both pickup and courier)
                            orders = cart_data.get("orders", [])
                            if orders:
                                fulfillment = orders[0].get("fulfillment", {})
                                # Check pickup first
                                pickup = fulfillment.get("pickupBooking", {})
                                if pickup:
                                    location = pickup.get("pickupLocation", {})
                                    if location and location.get("id"):
                                        credentials["store_id"] = location["id"]
                                        credentials["fulfillment_type"] = "pickup"
                                # Check courier/delivery
                                courier = fulfillment.get("courier", {})
                                if courier and courier.get("storeId"):
                                    credentials["store_id"] = courier["storeId"]
                                    credentials["fulfillment_type"] = "courier"
            except Exception as e:
                print(f"Could not fetch cart details: {e}")

        await browser.close()

        return credentials


def print_credentials(creds: dict):
    """Pretty print the extracted credentials."""
    print("\n" + "=" * 60)
    print("EXTRACTED API CREDENTIALS")
    print("=" * 60)

    print(f"\n📡 API Configuration:")
    print(f"   Base URL:  {creds['api_base_url']}")
    print(f"   API Key:   {creds['api_key']}")

    print(f"\n🔐 Authentication:")
    bearer = creds.get('bearer_token')
    if bearer:
        # Show truncated token for security
        print(f"   Bearer:    {bearer[:50]}...{bearer[-20:]} ({len(bearer)} chars)")
        # Decode and show expiration
        try:
            import base64
            from datetime import datetime
            payload_b64 = bearer.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            if exp:
                exp_time = datetime.fromtimestamp(exp)
                print(f"   Expires:   {exp_time.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception:
            pass
    else:
        print(f"   Bearer:    Not found (anonymous session)")

    print(f"\n🛒 Session Data:")
    print(f"   Cart ID:      {creds['cart_id'] or 'Not found'}")
    print(f"   Store ID:     {creds['store_id'] or 'Not found'}")
    print(f"   Fulfillment:  {creds.get('fulfillment_type') or 'Not found'}")
    print(f"   Order ID:     {creds['order_id'] or 'Not found'}")
    print(f"   User ID:      {creds['user_id'] or 'Not found (anonymous)'}")
    print(f"   Postal:       {creds['postal_code'] or 'Not found'}")

    if creds.get("cookies"):
        print(f"\n🍪 Relevant Cookies: {len(creds['cookies'])} found")
        for name in list(creds["cookies"].keys())[:5]:
            print(f"   - {name}")
        if len(creds["cookies"]) > 5:
            print(f"   ... and {len(creds['cookies']) - 5} more")

    print("\n" + "=" * 60)
    print("EXAMPLE CURL COMMANDS")
    print("=" * 60)

    if creds["cart_id"]:
        fulfillment = creds.get('fulfillment_type') or 'pickup'
        store_id = creds['store_id'] or '1545'
        bearer = creds.get('bearer_token')

        if bearer:
            # Authenticated cart - needs bearer token
            print(f"""
# Get cart contents (authenticated):
curl -s "{creds['api_base_url']}/carts/{creds['cart_id']}" \\
  -H "x-apikey: {creds['api_key']}" \\
  -H "authorization: bearer $BEARER_TOKEN" | jq '.orders[0].entries[] | {{name: .offer.product.name, qty: .quantity}}'

# Add item to cart (authenticated):
curl -s -X POST "{creds['api_base_url']}/carts/{creds['cart_id']}" \\
  -H "x-apikey: {creds['api_key']}" \\
  -H "authorization: bearer $BEARER_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{{"entries":{{"PRODUCT_CODE":{{"quantity":1,"fulfillmentMethod":"{fulfillment}","sellerId":"{store_id}"}}}}}}'

# Search for products (no auth needed):
curl -s -X POST "{creds['api_base_url']}/products/search" \\
  -H "x-apikey: {creds['api_key']}" \\
  -H "Content-Type: application/json" \\
  -d '{{"banner":"superstore","lang":"en","storeId":"{store_id}","term":"milk","pagination":{{"from":0,"size":5}}}}' | jq '.results[:3] | .[].name'

# Export bearer token for use in commands:
export BEARER_TOKEN="{bearer[:80]}..."
# (Full token saved to {creds.get('_output_file', 'api_credentials.json')})
""")
        else:
            # Anonymous cart - no bearer needed
            print(f"""
# Get cart contents (anonymous):
curl -s "{creds['api_base_url']}/carts/{creds['cart_id']}" \\
  -H "x-apikey: {creds['api_key']}" | jq '.orders[0].entries[] | .offer.product.name'

# Search for products:
curl -s -X POST "{creds['api_base_url']}/products/search" \\
  -H "x-apikey: {creds['api_key']}" \\
  -H "Content-Type: application/json" \\
  -d '{{"banner":"superstore","lang":"en","storeId":"{store_id}","term":"milk","pagination":{{"from":0,"size":5}}}}' | jq '.results[:3] | .[].name'

# Add item to cart (anonymous):
curl -s -X POST "{creds['api_base_url']}/carts/{creds['cart_id']}" \\
  -H "x-apikey: {creds['api_key']}" \\
  -H "Content-Type: application/json" \\
  -d '{{"entries":{{"PRODUCT_CODE":{{"quantity":1,"fulfillmentMethod":"{fulfillment}","sellerId":"{store_id}"}}}}}}'
""")
    else:
        print("\n⚠️  Cart ID not found. Try navigating to the cart in the browser first.")

    print("=" * 60)


def save_credentials(creds: dict, output_file: str = "api_credentials.json"):
    """Save credentials to a JSON file."""
    output_path = Path(output_file)

    # Don't save cookies to file for security, but do save bearer token
    save_data = {k: v for k, v in creds.items() if k != "cookies"}

    with open(output_path, "w") as f:
        json.dump(save_data, f, indent=2)

    print(f"\n💾 Credentials saved to: {output_path}")

    # Warn about token expiration
    if creds.get("bearer_token"):
        print("⚠️  Note: Bearer token expires (typically ~1 hour). Re-run to refresh.")


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract API credentials from logged-in browser session"
    )
    parser.add_argument(
        "--profile",
        default="./superstore-profile",
        help="Path to browser profile directory (default: ./superstore-profile)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default: headed)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="api_credentials.json",
        help="Output file for credentials (default: api_credentials.json)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save credentials to file",
    )

    args = parser.parse_args()

    print("🔍 Extracting API credentials from browser session...")

    creds = await extract_credentials(
        profile_dir=args.profile,
        headed=not args.headless,
    )

    if creds:
        creds["_output_file"] = args.output  # For display in curl examples
        print_credentials(creds)

        if not args.no_save:
            save_credentials(creds, args.output)
    else:
        print("❌ Failed to extract credentials")
        return 1

    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
