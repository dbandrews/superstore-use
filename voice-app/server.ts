import dotenv from "dotenv";
import express from "express";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, "..", ".env") });

const LOG_FILE = path.join(__dirname, "app.log");

function log(tag: string, msg: string) {
  const line = `[${new Date().toISOString()}] [${tag}] ${msg}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + "\n");
}

const app = express();
app.use(express.json());

// Log all API requests
app.use((req, _res, next) => {
  if (req.path.startsWith("/api") || req.path === "/token") {
    log("server", `${req.method} ${req.path}`);
  }
  next();
});

app.use(express.static(path.join(__dirname, "public")));

const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
if (!OPENAI_API_KEY) {
  console.error("Missing OPENAI_API_KEY in .env");
  process.exit(1);
}

const PCX_BASE = "https://api.pcexpress.ca/pcx-bff/api/v1";
const PCX_HEADERS: Record<string, string> = {
  "x-apikey": "C1xujSegT5j3ap3yexJjqhOfELwGKYvz",
  basesiteid: "superstore",
  "site-banner": "superstore",
  "x-loblaw-tenant-id": "ONLINE_GROCERIES",
  "x-channel": "web",
  "x-application-type": "web",
  "business-user-agent": "PCXWEB",
  "accept-language": "en",
  "content-type": "application/json",
};

const SYSTEM_PROMPT = `You are a friendly grocery shopping assistant for Real Canadian Superstore. Help users shop by voice. Start by asking where they're located - they can give you their address, neighbourhood, city, or postal code. Use whatever they give you to find the nearest stores. Present the top 3 stores and let them pick one. Then help them brainstorm simple recipes and build a shopping list. Keep responses concise since this is a voice conversation - avoid reading long lists. When adding items, search for products and confirm prices before adding. When the user is done, call finish_shopping and say goodbye.`;

const TOOLS = [
  {
    type: "function" as const,
    name: "find_nearest_stores",
    description: "Find the nearest Superstore pickup locations by address, neighbourhood, city, or postal code",
    parameters: {
      type: "object",
      properties: {
        location: { type: "string", description: "The user's location - can be a street address, neighbourhood, city, or postal code (e.g. '100 Main St, Calgary', 'Kensington Calgary', 'T2N 1A1')" },
      },
      required: ["location"],
    },
  },
  {
    type: "function" as const,
    name: "select_store",
    description: "Select a store and create a shopping cart for it",
    parameters: {
      type: "object",
      properties: {
        store_id: { type: "string", description: "The store ID to shop at" },
      },
      required: ["store_id"],
    },
  },
  {
    type: "function" as const,
    name: "search_products",
    description: "Search for products at the selected store",
    parameters: {
      type: "object",
      properties: {
        term: { type: "string", description: "Search term for the product" },
      },
      required: ["term"],
    },
  },
  {
    type: "function" as const,
    name: "add_to_cart",
    description: "Add items to the shopping cart",
    parameters: {
      type: "object",
      properties: {
        items: {
          type: "array",
          items: {
            type: "object",
            properties: {
              product_code: { type: "string" },
              quantity: { type: "number" },
            },
            required: ["product_code", "quantity"],
          },
          description: "Items to add with product code and quantity",
        },
      },
      required: ["items"],
    },
  },
  {
    type: "function" as const,
    name: "finish_shopping",
    description: "Signal that the user is done shopping",
    parameters: { type: "object", properties: {} },
  },
];

// ── GET /token ──────────────────────────────────────────────────────────
app.get("/token", async (_req, res) => {
  try {
    const resp = await fetch("https://api.openai.com/v1/realtime/sessions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${OPENAI_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "gpt-4o-realtime-preview-2025-06-03",
        voice: "alloy",
        instructions: SYSTEM_PROMPT,
        tools: TOOLS,
      }),
    });
    const data = await resp.json();
    log("server", `Token created, model=${data.model || "?"}`);
    res.json(data);
  } catch (err) {
    log("server", `Token error: ${err}`);
    res.status(500).json({ error: "Failed to create session" });
  }
});

// ── POST /api/find-stores ───────────────────────────────────────────────
app.post("/api/find-stores", async (req, res) => {
  try {
    const { location, postal_code } = req.body;
    const query = location || postal_code || "";

    // Geocode the user's location (address, neighbourhood, city, or postal code)
    const geoResp = await fetch(
      `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)},+Canada&format=json&limit=1`,
      { headers: { "User-Agent": "superstore-voice-app" } }
    );
    const geoData: any[] = await geoResp.json();
    if (!geoData.length) {
      return res.status(400).json({ error: `Could not find location: "${query}"` });
    }
    const lat = parseFloat(geoData[0].lat);
    const lng = parseFloat(geoData[0].lon);
    log("server", `Geocoded "${query}" → ${lat}, ${lng} (${geoData[0].display_name})`);

    // Fetch pickup locations
    const locResp = await fetch(
      `${PCX_BASE}/pickup-locations?bannerIds=superstore`,
      { headers: PCX_HEADERS }
    );
    const locData: any = await locResp.json();
    const locations: any[] = locData.pickupLocations ?? locData ?? [];

    // Sort by distance
    const withDist = locations.map((loc: any) => {
      const gp = loc.geoPoint ?? {};
      const dLat = ((gp.latitude ?? 0) - lat) * Math.PI / 180;
      const dLng = ((gp.longitude ?? 0) - lng) * Math.PI / 180;
      const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(lat * Math.PI / 180) *
          Math.cos((gp.latitude ?? 0) * Math.PI / 180) *
          Math.sin(dLng / 2) ** 2;
      const dist = 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
      return { ...loc, _dist: dist };
    });
    withDist.sort((a: any, b: any) => a._dist - b._dist);

    const top3 = withDist.slice(0, 3).map((loc: any) => ({
      storeId: loc.storeId,
      name: loc.name,
      address: loc.address?.formattedAddress ?? "",
      distance_km: Math.round(loc._dist * 10) / 10,
    }));

    res.json({ stores: top3 });
  } catch (err) {
    console.error("Find stores error:", err);
    res.status(500).json({ error: "Failed to find stores" });
  }
});

// ── POST /api/create-cart ───────────────────────────────────────────────
app.post("/api/create-cart", async (req, res) => {
  try {
    const { store_id } = req.body;
    const resp = await fetch(`${PCX_BASE}/carts`, {
      method: "POST",
      headers: PCX_HEADERS,
      body: JSON.stringify({
        bannerId: "superstore",
        language: "en",
        storeId: store_id,
      }),
    });
    const data: any = await resp.json();
    res.json({ cart_id: data.cartId ?? data.id });
  } catch (err) {
    console.error("Create cart error:", err);
    res.status(500).json({ error: "Failed to create cart" });
  }
});

// ── POST /api/search-products ───────────────────────────────────────────
app.post("/api/search-products", async (req, res) => {
  try {
    const { term, store_id, cart_id } = req.body;
    const resp = await fetch(`${PCX_BASE}/products/search`, {
      method: "POST",
      headers: PCX_HEADERS,
      body: JSON.stringify({
        term,
        banner: "superstore",
        storeId: store_id,
        lang: "en",
        cartId: cart_id,
        pagination: { from: 0, size: 10 },
      }),
    });
    const data: any = await resp.json();
    const results = (data.results ?? []).map((p: any) => ({
      code: p.code,
      name: p.name,
      brand: p.brand,
      price: p.prices?.price?.value ?? p.price,
      unit: p.prices?.price?.unit ?? "",
    }));
    res.json({ products: results });
  } catch (err) {
    console.error("Search error:", err);
    res.status(500).json({ error: "Failed to search products" });
  }
});

// ── POST /api/add-to-cart ───────────────────────────────────────────────
app.post("/api/add-to-cart", async (req, res) => {
  try {
    const { cart_id, store_id, items } = req.body;
    const entries: Record<string, any> = {};
    for (const item of items) {
      entries[item.product_code] = {
        quantity: item.quantity,
        fulfillmentMethod: "pickup",
        sellerId: store_id,
      };
    }
    const resp = await fetch(`${PCX_BASE}/carts/${cart_id}`, {
      method: "POST",
      headers: PCX_HEADERS,
      body: JSON.stringify({ entries }),
    });
    const data: any = await resp.json();
    res.json({ success: true, cart: data });
  } catch (err) {
    console.error("Add to cart error:", err);
    res.status(500).json({ error: "Failed to add to cart" });
  }
});

// ── POST /api/log ─────────────────────────────────────────────────────
app.post("/api/log", (req, res) => {
  const { level, msg } = req.body;
  log(`client:${level || "info"}`, msg || "");
  res.json({ ok: true });
});

// ── POST /api/finish-shopping ──────────────────────────────────────────
app.post("/api/finish-shopping", (_req, res) => {
  res.json({ success: true, message: "Shopping session complete" });
});

const PORT = process.env.PORT ?? 3000;
app.listen(PORT, () => console.log(`Server running on http://localhost:${PORT}`));
