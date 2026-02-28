import modal

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("fastapi", "httpx")
    .add_local_dir("./voice-app/public", remote_path="/app/public")
)

app = modal.App("pc-express-voice")

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

BANNERS = {
    "superstore": {"name": "Real Canadian Superstore", "cart_url": "https://www.realcanadiansuperstore.ca/en/cartReview"},
    "nofrills": {"name": "No Frills", "cart_url": "https://www.nofrills.ca/en/cartReview"},
    "loblaw": {"name": "Loblaws", "cart_url": "https://www.loblaws.ca/en/cartReview"},
    "independent": {"name": "Your Independent Grocer", "cart_url": "https://www.yourindependentgrocer.ca/en/cartReview"},
    "zehrs": {"name": "Zehrs", "cart_url": "https://www.zehrs.ca/en/cartReview"},
    "fortinos": {"name": "Fortinos", "cart_url": "https://www.fortinos.ca/en/cartReview"},
    "maxi": {"name": "Maxi", "cart_url": "https://www.maxi.ca/en/cartReview"},
    "provigo": {"name": "Provigo", "cart_url": "https://www.provigo.ca/en/cartReview"},
    "dominion": {"name": "Dominion", "cart_url": "https://www.dominion.ca/en/cartReview"},
    "wholesaleclub": {"name": "Wholesale Club", "cart_url": "https://www.wholesaleclub.ca/en/cartReview"},
    "valumart": {"name": "Valu-Mart", "cart_url": "https://www.valumart.ca/en/cartReview"},
    "extrafoods": {"name": "Extra Foods", "cart_url": "https://www.extrafoods.ca/en/cartReview"},
}


def pcx_headers(banner: str = "superstore") -> dict:
    return {**PCX_BASE_HEADERS, "basesiteid": banner, "site-banner": banner}

SYSTEM_PROMPT = (
    "You are a friendly grocery shopping assistant for PC Express. "
    "Help users shop by voice. Start by asking where they're located - they can give "
    "you their address, neighbourhood, city, or postal code. Use whatever they give you "
    "to find the nearest PC Express pickup locations across all Loblaw banners "
    "(Superstore, No Frills, Loblaws, Independent, Zehrs, Fortinos, Maxi, Provigo, etc.). "
    "Present the top 3 closest stores and let them pick one. Then "
    "help them brainstorm simple recipes and build a shopping list. Keep responses concise "
    "since this is a voice conversation - avoid reading long lists. When adding items, "
    "search for products and confirm prices before adding, unless the user is very confident and gives a list of items to add to the cart"
    ". In this case, immediately search for each item and select the most appropriate match to add to the cart for each. "
    "After adding items, check the response for failed_items and inform the user about any items that couldn't be added. "
    "If the user seems done adding all items, remind them to let you know they are done and you'll give them the link to the cart. "
    "When the user is done, call "
    "finish_shopping and say goodbye and let them know they can fine tune their cart by clicking the link."
)

TOOLS = [
    {
        "type": "function",
        "name": "find_nearest_stores",
        "description": "Find the nearest PC Express pickup locations by address, neighbourhood, city, or postal code",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The user's location - can be a street address, neighbourhood, city, or postal code (e.g. '100 Main St, Calgary', 'Kensington Calgary', 'T2N 1A1')",
                },
            },
            "required": ["location"],
        },
    },
    {
        "type": "function",
        "name": "select_store",
        "description": "Select a store and create a shopping cart for it. Use the store_id and banner from find_nearest_stores results.",
        "parameters": {
            "type": "object",
            "properties": {
                "store_id": {"type": "string", "description": "The store ID to shop at"},
                "banner": {"type": "string", "description": "The banner ID of the store (e.g. superstore, nofrills, loblaw)"},
            },
            "required": ["store_id", "banner"],
        },
    },
    {
        "type": "function",
        "name": "search_products",
        "description": "Search for products at the selected store. Only returns in-stock products.",
        "parameters": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Search term for the product"},
            },
            "required": ["term"],
        },
    },
    {
        "type": "function",
        "name": "add_to_cart",
        "description": "Add items to the shopping cart. Returns added_items (successfully added) and failed_items (with reason). Check failed_items and inform the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_code": {"type": "string"},
                            "quantity": {"type": "number"},
                        },
                        "required": ["product_code", "quantity"],
                    },
                    "description": "Items to add with product code and quantity",
                },
            },
            "required": ["items"],
        },
    },
    {
        "type": "function",
        "name": "finish_shopping",
        "description": "Signal that the user is done shopping",
        "parameters": {"type": "object", "properties": {}},
    },
]


def create_web_app():
    import json
    import math
    import os
    import re
    from urllib.parse import quote

    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.base import BaseHTTPMiddleware

    web_app = FastAPI()

    class NoCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-cache"
            return response

    web_app.add_middleware(NoCacheMiddleware)

    @web_app.get("/token")
    async def get_token():
        api_key = os.environ["OPENAI_API_KEY"]
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/realtime/sessions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-realtime-mini",
                    "voice": "cedar",
                    "instructions": SYSTEM_PROMPT,
                    "tools": TOOLS,
                    "input_audio_transcription": {
                        "model": "whisper-1",
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.75,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                    },
                    "input_audio_noise_reduction": {
                        "type": "near_field",
                    },
                },
            )
        data = resp.json()
        print(f"[token] created, model={data.get('model', '?')}")
        return data

    @web_app.post("/api/find-stores")
    async def find_stores(request: Request):
        import asyncio

        body = await request.json()
        query = body.get("location") or body.get("postal_code") or ""
        print(f'[find-stores] Received query: "{query}"')

        # Canadian postal code: A1A 1A1 (letter-digit-letter space? digit-letter-digit)
        CA_POSTAL_RE = re.compile(
            r"^([A-Za-z]\d[A-Za-z])\s*(\d[A-Za-z]\d)$"
        )

        def normalize_postal(raw: str) -> str | None:
            """If *raw* looks like a Canadian postal code, return it as 'A1A 1A1' (uppercase, single space)."""
            m = CA_POSTAL_RE.match(raw.strip())
            if not m:
                return None
            return f"{m.group(1).upper()} {m.group(2).upper()}"

        def normalize_address(addr: str) -> str:
            """Shorten verbose directionals for better geocoding matches."""
            replacements = {
                r"\bNorthwest\b": "NW",
                r"\bNortheast\b": "NE",
                r"\bSouthwest\b": "SW",
                r"\bSoutheast\b": "SE",
            }
            for pattern, abbr in replacements.items():
                addr = re.sub(pattern, abbr, addr, flags=re.IGNORECASE)
            return addr

        async def geocode(q: str, client: httpx.AsyncClient):
            mapbox_token = os.environ.get("MAPBOX_API_KEY", "")
            url = (
                f"https://api.mapbox.com/search/geocode/v6/forward"
                f"?q={quote(q)}&country=ca&limit=1&access_token={mapbox_token}"
            )
            print(f'[find-stores] Mapbox geocode request: "{q}"')
            resp = await client.get(url)
            print(f"[find-stores] Mapbox HTTP {resp.status_code}, body length={len(resp.text)}")
            if resp.status_code != 200:
                print(f"[find-stores] Mapbox error response: {resp.text[:500]}")
                return None
            data = resp.json()
            features = data.get("features", [])
            if not features:
                print(f'[find-stores] Mapbox returned no features for "{q}"')
                return None
            feat = features[0]
            coords = feat["geometry"]["coordinates"]  # [lon, lat]
            display = feat.get("properties", {}).get("full_address") or feat.get("properties", {}).get("name", "")
            hit = {"lat": str(coords[1]), "lon": str(coords[0]), "display_name": display}
            print(f'[find-stores] Geocoded "{q}" -> {hit["lat"]}, {hit["lon"]} ({hit["display_name"]})')
            return hit

        async def fetch_banner_locations(banner: str, client: httpx.AsyncClient):
            try:
                resp = await client.get(
                    f"{PCX_BASE}/pickup-locations?bannerIds={banner}",
                    headers=pcx_headers(banner),
                )
                data = resp.json()
                locs = data if isinstance(data, list) else data.get("pickupLocations", [])
                print(f"[find-stores] {banner}: {len(locs)} locations")
                return locs
            except Exception as e:
                print(f"[find-stores] {banner}: error fetching locations: {e}")
                return []

        async with httpx.AsyncClient() as client:
            # Normalize Canadian postal codes (e.g. "t2k 0a5" -> "T2K 0A5")
            postal = normalize_postal(query)
            if postal and postal != query:
                print(f'[find-stores] Normalized postal code: "{query}" -> "{postal}"')
                query = postal

            geo_hit = await geocode(query, client)
            if not geo_hit:
                normalized = normalize_address(query)
                if normalized != query:
                    print(f'[find-stores] Retrying with normalized address: "{normalized}"')
                    geo_hit = await geocode(normalized, client)
            if not geo_hit:
                print(f'[find-stores] FAILED to geocode "{query}"')
                return JSONResponse(
                    status_code=400,
                    content={"error": f'Could not find location: "{query}"'},
                )
            lat = float(geo_hit["lat"])
            lng = float(geo_hit["lon"])

            # Query all banners in parallel
            banner_tasks = {
                banner: fetch_banner_locations(banner, client)
                for banner in BANNERS
            }
            results = await asyncio.gather(*banner_tasks.values())
            all_locations = []
            for locs in results:
                all_locations.extend(locs)
            print(f"[find-stores] Total locations across all banners: {len(all_locations)}")

        def distance(loc):
            gp = loc.get("geoPoint", {})
            d_lat = (gp.get("latitude", 0) - lat) * math.pi / 180
            d_lng = (gp.get("longitude", 0) - lng) * math.pi / 180
            a = (
                math.sin(d_lat / 2) ** 2
                + math.cos(lat * math.pi / 180)
                * math.cos(gp.get("latitude", 0) * math.pi / 180)
                * math.sin(d_lng / 2) ** 2
            )
            return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        sorted_locs = sorted(all_locations, key=distance)
        top3 = [
            {
                "storeId": loc.get("storeId"),
                "name": loc.get("name"),
                "banner": loc.get("storeBannerId", "superstore"),
                "bannerName": loc.get("storeBannerName", ""),
                "address": (loc.get("address") or {}).get("formattedAddress", ""),
                "distance_km": round(distance(loc) * 10) / 10,
            }
            for loc in sorted_locs[:3]
        ]
        for s in top3:
            print(f"[find-stores]   #{s['storeId']} [{s['banner']}] {s['name']} — {s['distance_km']}km — {s['address']}")
        return {"stores": top3}

    @web_app.post("/api/create-cart")
    async def create_cart(request: Request):
        body = await request.json()
        store_id = body.get("store_id")
        banner = body.get("banner", "superstore")
        print(f"[create-cart] Creating cart for store_id={store_id}, banner={banner}")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{PCX_BASE}/carts",
                headers=pcx_headers(banner),
                json={"bannerId": banner, "language": "en", "storeId": store_id},
            )
        data = resp.json()
        cart_id = data.get("cartId") or data.get("id")
        cart_url = BANNERS.get(banner, BANNERS["superstore"])["cart_url"]
        print(f"[create-cart] HTTP {resp.status_code}, cart_id={cart_id}")
        if resp.status_code != 200:
            print(f"[create-cart] Error response: {json.dumps(data, indent=2)}")
        return {"cart_id": cart_id, "banner": banner, "cart_url": cart_url}

    @web_app.post("/api/search-products")
    async def search_products(request: Request):
        body = await request.json()
        term = body.get("term")
        store_id = body.get("store_id")
        banner = body.get("banner", "superstore")
        print(f'[search] Searching "{term}" at store {store_id} (banner={banner})')
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{PCX_BASE}/products/search",
                headers=pcx_headers(banner),
                json={
                    "term": term,
                    "banner": banner,
                    "storeId": store_id,
                    "lang": "en",
                    "cartId": body.get("cart_id"),
                    "pagination": {"from": 0, "size": 10},
                },
            )
        data = resp.json()
        total_results = data.get("pagination", {}).get("totalResults", "?")
        all_results = data.get("results", [])
        print(f"[search] HTTP {resp.status_code}, {total_results} total results, {len(all_results)} returned")
        if resp.status_code != 200:
            print(f"[search] Error response: {json.dumps(data, indent=2)}")
        results = []
        for p in all_results:
            shoppable = p.get("shoppable", True)
            stock = p.get("stockStatus", "OK")
            if not shoppable or stock != "OK":
                print(f"[search]   SKIP {p.get('code')} {p.get('name')!r} (shoppable={shoppable}, stock={stock})")
                continue
            price_obj = p.get("prices", {}).get("price", {}) or {}
            item = {
                "code": p.get("code"),
                "name": p.get("name"),
                "brand": p.get("brand"),
                "price": price_obj.get("value") or p.get("price"),
                "unit": price_obj.get("unit", ""),
            }
            print(f"[search]   {item['code']} {item['brand'] or ''} {item['name']!r} ${item['price']} / {item['unit']}")
            results.append(item)
        return {"products": results}

    @web_app.post("/api/add-to-cart")
    async def add_to_cart(request: Request):
        body = await request.json()
        cart_id = body.get("cart_id")
        store_id = body.get("store_id")
        banner = body.get("banner", "superstore")
        items = body.get("items", [])

        print(f"[add-to-cart] Adding {len(items)} item(s) to cart {cart_id} at store {store_id} (banner={banner})")
        for item in items:
            print(f"[add-to-cart]   {item['product_code']} x{item['quantity']}")

        entries = {}
        for item in items:
            entries[item["product_code"]] = {
                "quantity": item["quantity"],
                "fulfillmentMethod": "pickup",
                "sellerId": store_id,
            }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{PCX_BASE}/carts/{cart_id}",
                headers=pcx_headers(banner),
                json={"entries": entries},
            )
        data = resp.json()
        print(f"[add-to-cart] HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"[add-to-cart] Error response: {json.dumps(data, indent=2)}")

        # Parse which items actually made it into the cart
        requested_codes = {item["product_code"] for item in items}
        added_codes = set()
        added_items = []
        cart_obj = data.get("cart", data)  # response may nest under "cart" or be top-level
        for order in cart_obj.get("orders", []):
            for entry in order.get("entries", []):
                product = entry.get("offer", {}).get("product", {})
                code = product.get("code") or product.get("id", "")
                if code in requested_codes:
                    added_codes.add(code)
                    name = entry.get("offer", {}).get("product", {}).get("name", "")
                    qty = entry.get("quantity", 0)
                    added_items.append(
                        {
                            "product_code": code,
                            "name": name,
                            "quantity": qty,
                        }
                    )
                    print(f"[add-to-cart]   OK {code} {name!r} x{qty}")

        # Build failed items from API errors + codes not found in cart
        failed_items = []
        for err in data.get("errors", []):
            reason = err.get("message", "Unknown error")
            pc = err.get("productCode", "")
            failed_items.append({"product_code": pc, "reason": reason})
            print(f"[add-to-cart]   FAIL {pc}: {reason}")
        failed_codes = {f["product_code"] for f in failed_items}
        for item in items:
            code = item["product_code"]
            if code not in added_codes and code not in failed_codes:
                reason = "Item not found in cart after adding — may be unavailable"
                failed_items.append({"product_code": code, "reason": reason})
                print(f"[add-to-cart]   MISSING {code}: {reason}")

        success = len(added_items) > 0
        print(f"[add-to-cart] Result: {len(added_items)} added, {len(failed_items)} failed")
        return {
            "success": success,
            "added_items": added_items,
            "failed_items": failed_items,
        }

    @web_app.post("/api/finish-shopping")
    async def finish_shopping(request: Request):
        body = await request.json()
        cart_id = body.get("cart_id")
        banner = body.get("banner", "superstore")
        base_url = BANNERS.get(banner, BANNERS["superstore"])["cart_url"]
        cart_url = f"{base_url}?forceCartId={cart_id}" if cart_id else None
        print(f"[finish-shopping] cart_id={cart_id}, banner={banner}, cart_url={cart_url}")
        return {"success": True, "message": "Shopping session complete", "cart_url": cart_url}

    @web_app.get("/")
    async def index():
        return FileResponse("/app/public/index.html")

    web_app.mount("/", StaticFiles(directory="/app/public"), name="static")

    return web_app


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("openai-secret"), modal.Secret.from_name("mapbox-api-key")],
    timeout=3600,
    cpu=0.25,
    memory=256,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def serve():
    return create_web_app()
