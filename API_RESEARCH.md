# Real Canadian Superstore API Research

This document summarizes the API endpoints discovered for programmatic interaction with Real Canadian Superstore's online grocery system.

## API Overview

**Base URL:** `https://api.pcexpress.ca/pcx-bff/api/v1/`

**Authentication:** Public API key (no user authentication required for cart operations)

## Required Headers

```http
x-apikey: C1xujSegT5j3ap3yexJjqhOfELwGKYvz
basesiteid: superstore
site-banner: superstore
x-loblaw-tenant-id: ONLINE_GROCERIES
x-channel: web
x-application-type: web
business-user-agent: PCXWEB
accept-language: en
content-type: application/json
accept: application/json
```

## Endpoints

### 1. Create Cart

Creates a new anonymous shopping cart.

**Endpoint:** `POST /api/v1/carts`

**Request:**
```json
{
  "bannerId": "superstore",
  "language": "en",
  "storeId": "1545"
}
```

**Response:**
```json
{
  "id": "cff9f1b4-05e3-4cc0-a1cb-e9ce5d0b97e5",
  "status": "OPEN",
  "orders": [...]
}
```

**Notes:**
- Cart ID is a UUID
- Cart persists server-side (timeout unknown)
- Store ID determines pricing and availability

---

### 2. Get Cart

Retrieves current cart contents.

**Endpoint:** `GET /api/v1/carts/{cartId}`

**Response:** Full cart object with entries, prices, and totals.

---

### 3. Product Search

Search for products by term.

**Endpoint:** `POST /api/v1/products/search`

**Request:**
```json
{
  "term": "milk",
  "banner": "superstore",
  "storeId": "1545",
  "lang": "en",
  "cartId": "your-cart-id",
  "pagination": {
    "from": 0,
    "size": 20
  }
}
```

**Response:**
```json
{
  "pagination": {
    "from": 0,
    "size": 20,
    "totalResults": 656
  },
  "results": [
    {
      "code": "20657896_EA",
      "name": "Homogenized Milk 3.25%",
      "brand": "Beatrice",
      "prices": {
        "price": {"value": 5.84, "unit": "ea"}
      },
      "pricingUnits": {
        "type": "SOLD_BY_EACH",
        "unit": "ea",
        "minOrderQuantity": 1,
        "maxOrderQuantity": 24
      }
    }
  ]
}
```

---

### 4. Add to Cart

Add one or more products to cart.

**Endpoint:** `POST /api/v1/carts/{cartId}`

**Request:**
```json
{
  "entries": {
    "20028593001_EA": {
      "quantity": 3,
      "fulfillmentMethod": "pickup",
      "sellerId": "1545"
    },
    "20132621001_KG": {
      "quantity": 2,
      "fulfillmentMethod": "pickup",
      "sellerId": "1545"
    }
  }
}
```

**Notes:**
- Can add multiple products in one request
- `sellerId` is the store ID
- `fulfillmentMethod`: "pickup" or "delivery"

---

### 5. Type-ahead Search

For autocomplete suggestions.

**Endpoint:** `POST /api/v1/products/type-ahead`

**Request:**
```json
{
  "banner": "superstore",
  "lang": "en",
  "storeId": "1545",
  "term": "apples",
  "cartId": "your-cart-id",
  "size": 6
}
```

---

### 6. Pickup Locations

Get available store locations.

**Endpoint:** `GET /api/v1/pickup-locations?bannerIds=superstore`

---

### 7. Fulfillment Time Slots

Get available pickup/delivery times.

**Endpoint:** `GET /api/v1/fulfillment/next-available-plan-timeslots`

**Query Parameters:**
- `banner=superstore`
- `cartId={cartId}`
- `locationId={storeId}`
- `postalCode={postalCode}`

---

## Product ID Format

Product codes follow the pattern: `{productNumber}_{unit}`

| Suffix | Selling Type | Example | Description |
|--------|-------------|---------|-------------|
| `_EA` | `SOLD_BY_EACH` | `20028593001_EA` | Standard unit items (lemons, milk cartons) |
| `_KG` | `SOLD_BY_EACH_PRICED_BY_WEIGHT` | `20132621001_KG` | Individual items priced by weight (apples) |
| `_C12` | Case | `21508926_C12` | Bulk packs (case of 12 cans) |

---

## Cart ID Persistence

| Storage Location | Key |
|------------------|-----|
| localStorage | `ANONYMOUS_CART_ID` |
| localStorage | `lcl-cart-id-banner` |

**Behavior:**
- Cart ID persists in browser localStorage
- Same cart shared across tabs (same origin)
- New cart created if localStorage cleared
- Different browser profiles = different carts

---

## Store IDs

Example stores discovered:
- `1545` - Real Canadian Superstore 4th Street, Calgary, AB

Store ID is required for:
- Creating carts
- Product searches (affects pricing/availability)
- Adding items to cart (`sellerId`)

---

## Validated Test Cases

| Item | Product Code | Qty | Unit Price | Total |
|------|-------------|-----|------------|-------|
| Lemon | `20028593001_EA` | 3 | $1.00 | $3.00 |
| Honeycrisp Apples | `20132621001_KG` | 2 | $1.95 | $3.91 |
| 2% Milk (Beatrice) | `20658152_EA` | 1 | $5.84 | $5.84 |

---

## Next Steps for Validation

### 1. Test Cart Persistence
```bash
# Create cart, wait 24+ hours, try to access
curl -X GET "https://api.pcexpress.ca/pcx-bff/api/v1/carts/{cartId}" \
  -H "x-apikey: C1xujSegT5j3ap3yexJjqhOfELwGKYvz" \
  -H "basesiteid: superstore" \
  -H "site-banner: superstore"
```

### 2. Test Rate Limiting
- Make rapid sequential requests
- Document any 429 responses or throttling

### 3. Test Update/Remove from Cart
```bash
# Try updating quantity (likely same endpoint with different quantity)
# Try setting quantity to 0 to remove
```

### 4. Test Checkout Flow
- Investigate authentication requirements
- Find checkout API endpoints
- Determine if checkout requires login

### 5. Test Different Banners
Other Loblaw banners that may use same API:
- `loblaws`
- `nofrills`
- `zehrs`
- `fortinos`

### 6. Implement Python Client

```python
import requests

class SuperstoreAPI:
    BASE_URL = "https://api.pcexpress.ca/pcx-bff/api/v1"

    HEADERS = {
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

    def __init__(self, store_id: str = "1545"):
        self.store_id = store_id
        self.cart_id = None

    def create_cart(self) -> str:
        resp = requests.post(
            f"{self.BASE_URL}/carts",
            headers=self.HEADERS,
            json={
                "bannerId": "superstore",
                "language": "en",
                "storeId": self.store_id
            }
        )
        data = resp.json()
        self.cart_id = data["id"]
        return self.cart_id

    def search(self, term: str, size: int = 20) -> list:
        resp = requests.post(
            f"{self.BASE_URL}/products/search",
            headers=self.HEADERS,
            json={
                "term": term,
                "banner": "superstore",
                "storeId": self.store_id,
                "lang": "en",
                "cartId": self.cart_id,
                "pagination": {"from": 0, "size": size}
            }
        )
        return resp.json().get("results", [])

    def add_to_cart(self, items: dict) -> dict:
        """
        items: {"product_code": quantity, ...}
        e.g., {"20028593001_EA": 3, "20132621001_KG": 2}
        """
        entries = {
            code: {
                "quantity": qty,
                "fulfillmentMethod": "pickup",
                "sellerId": self.store_id
            }
            for code, qty in items.items()
        }

        resp = requests.post(
            f"{self.BASE_URL}/carts/{self.cart_id}",
            headers=self.HEADERS,
            json={"entries": entries}
        )
        return resp.json()

    def get_cart(self) -> dict:
        resp = requests.get(
            f"{self.BASE_URL}/carts/{self.cart_id}",
            headers=self.HEADERS
        )
        return resp.json()
```

### 7. Integration with Existing Agent

Replace browser automation with direct API calls:
1. Use `search()` instead of browser navigation
2. Use `add_to_cart()` instead of clicking buttons
3. Keep browser automation only for checkout (if login required)

---

## Limitations & Considerations

1. **Authentication**: Checkout likely requires user login (PC Optimum account)
2. **Rate Limits**: Unknown, needs testing
3. **API Stability**: Internal API, may change without notice
4. **Terms of Service**: Review Loblaw's ToS for API usage policies
5. **Regional Availability**: Store IDs and products vary by region

---

## Related Files

- `main.py` - Current browser-based CLI agent
- `modal_app.py` - Cloud deployment using browser automation
- `login.py` - Browser-based login flow (may still be needed for checkout)
