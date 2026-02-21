import modal

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("fastapi", "httpx")
    .add_local_dir("./voice-app/public", remote_path="/app/public")
)

app = modal.App("superstore-voice")

PCX_BASE = "https://api.pcexpress.ca/pcx-bff/api/v1"
PCX_HEADERS = {
    "x-apikey": "C1xujSegT5j3ap3yexJjqhOfELwGKYvz",
    "basesiteid": "superstore",
    "site-banner": "superstore",
    "x-loblaw-tenant-id": "ONLINE_GROCERIES",
    "x-channel": "web",
    "x-application-type": "web",
    "business-user-agent": "PCXWEB",
    "accept-language": "en",
    "content-type": "application/json",
}

SYSTEM_PROMPT = (
    "You are a friendly grocery shopping assistant for Real Canadian Superstore. "
    "Help users shop by voice. Start by asking where they're located - they can give "
    "you their address, neighbourhood, city, or postal code. Use whatever they give you "
    "to find the nearest stores. Present the top 3 stores and let them pick one. Then "
    "help them brainstorm simple recipes and build a shopping list. Keep responses concise "
    "since this is a voice conversation - avoid reading long lists. When adding items, "
    "search for products and confirm prices before adding. When the user is done, call "
    "finish_shopping and say goodbye."
)

TOOLS = [
    {
        "type": "function",
        "name": "find_nearest_stores",
        "description": "Find the nearest Superstore pickup locations by address, neighbourhood, city, or postal code",
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
        "description": "Select a store and create a shopping cart for it",
        "parameters": {
            "type": "object",
            "properties": {
                "store_id": {"type": "string", "description": "The store ID to shop at"},
            },
            "required": ["store_id"],
        },
    },
    {
        "type": "function",
        "name": "search_products",
        "description": "Search for products at the selected store",
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
        "description": "Add items to the shopping cart",
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
    import math
    import os
    from urllib.parse import quote

    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    web_app = FastAPI()

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
                    "voice": "alloy",
                    "instructions": SYSTEM_PROMPT,
                    "tools": TOOLS,
                    "input_audio_transcription": {
                        "model": "whisper-1",
                    },
                },
            )
        data = resp.json()
        print(f"[token] created, model={data.get('model', '?')}")
        return data

    @web_app.post("/api/find-stores")
    async def find_stores(request: Request):
        body = await request.json()
        query = body.get("location") or body.get("postal_code") or ""

        async with httpx.AsyncClient() as client:
            geo_resp = await client.get(
                f"https://nominatim.openstreetmap.org/search?q={quote(query)},+Canada&format=json&limit=1",
                headers={"User-Agent": "superstore-voice-app"},
            )
            geo_data = geo_resp.json()
            if not geo_data:
                return JSONResponse(
                    status_code=400,
                    content={"error": f'Could not find location: "{query}"'},
                )
            lat = float(geo_data[0]["lat"])
            lng = float(geo_data[0]["lon"])
            print(f'[find-stores] Geocoded "{query}" -> {lat}, {lng}')

            loc_resp = await client.get(
                f"{PCX_BASE}/pickup-locations?bannerIds=superstore",
                headers=PCX_HEADERS,
            )
            loc_data = loc_resp.json()

        if isinstance(loc_data, list):
            locations = loc_data
        else:
            locations = loc_data.get("pickupLocations", [])

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

        sorted_locs = sorted(locations, key=distance)
        top3 = [
            {
                "storeId": loc.get("storeId"),
                "name": loc.get("name"),
                "address": (loc.get("address") or {}).get("formattedAddress", ""),
                "distance_km": round(distance(loc) * 10) / 10,
            }
            for loc in sorted_locs[:3]
        ]
        return {"stores": top3}

    @web_app.post("/api/create-cart")
    async def create_cart(request: Request):
        body = await request.json()
        store_id = body.get("store_id")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{PCX_BASE}/carts",
                headers=PCX_HEADERS,
                json={"bannerId": "superstore", "language": "en", "storeId": store_id},
            )
        data = resp.json()
        return {"cart_id": data.get("cartId") or data.get("id")}

    @web_app.post("/api/search-products")
    async def search_products(request: Request):
        body = await request.json()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{PCX_BASE}/products/search",
                headers=PCX_HEADERS,
                json={
                    "term": body.get("term"),
                    "banner": "superstore",
                    "storeId": body.get("store_id"),
                    "lang": "en",
                    "cartId": body.get("cart_id"),
                    "pagination": {"from": 0, "size": 10},
                },
            )
        data = resp.json()
        results = [
            {
                "code": p.get("code"),
                "name": p.get("name"),
                "brand": p.get("brand"),
                "price": (p.get("prices", {}).get("price", {}) or {}).get("value") or p.get("price"),
                "unit": (p.get("prices", {}).get("price", {}) or {}).get("unit", ""),
            }
            for p in data.get("results", [])
        ]
        return {"products": results}

    @web_app.post("/api/add-to-cart")
    async def add_to_cart(request: Request):
        body = await request.json()
        cart_id = body.get("cart_id")
        store_id = body.get("store_id")
        items = body.get("items", [])

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
                headers=PCX_HEADERS,
                json={"entries": entries},
            )
        data = resp.json()
        return {"success": True, "cart": data}

    @web_app.post("/api/log")
    async def log_endpoint(request: Request):
        body = await request.json()
        level = body.get("level", "info")
        msg = body.get("msg", "")
        print(f"[client:{level}] {msg}")
        return {"ok": True}

    @web_app.post("/api/finish-shopping")
    async def finish_shopping():
        return {"success": True, "message": "Shopping session complete"}

    @web_app.get("/")
    async def index():
        return FileResponse("/app/public/index.html")

    web_app.mount("/", StaticFiles(directory="/app/public"), name="static")

    return web_app


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("openai-secret")],
    timeout=3600,
    cpu=0.25,
    memory=256,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def serve():
    return create_web_app()
