"""Superstore API Client.

Provides typed methods for interacting with the Real Canadian Superstore API.
Based on research documented in feat/api-research branch.

API Base: https://api.pcexpress.ca/pcx-bff/api/v1/
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.api.credentials import SuperstoreCredentials, DEFAULT_API_KEY, DEFAULT_STORE_ID


API_BASE_URL = "https://api.pcexpress.ca/pcx-bff/api/v1"

# Required headers for all API calls
API_HEADERS = {
    "x-apikey": DEFAULT_API_KEY,
    "basesiteid": "superstore",
    "site-banner": "superstore",
    "x-loblaw-tenant-id": "ONLINE_GROCERIES",
    "x-channel": "web",
    "x-application-type": "web",
    "business-user-agent": "PCXWEB",
    "accept-language": "en",
    "content-type": "application/json",
    "accept": "application/json",
}


@dataclass
class ProductSearchResult:
    """A product from search results.

    Attributes:
        code: Product code (e.g., "20657896_EA")
        name: Product name (e.g., "Homogenized Milk 3.25%")
        brand: Brand name (e.g., "Beatrice")
        price: Price value
        unit: Price unit (e.g., "ea" or "kg")
        description: Full product description
        min_quantity: Minimum order quantity
        max_quantity: Maximum order quantity
        image_url: Product image URL
        selling_type: How product is sold (SOLD_BY_EACH, SOLD_BY_EACH_PRICED_BY_WEIGHT)
    """

    code: str
    name: str
    brand: str = ""
    price: float = 0.0
    unit: str = "ea"
    description: str = ""
    min_quantity: int = 1
    max_quantity: int = 99
    image_url: str | None = None
    selling_type: str = "SOLD_BY_EACH"

    def display_name(self) -> str:
        """Get display-friendly name with brand."""
        if self.brand:
            return f"{self.brand} - {self.name}"
        return self.name


@dataclass
class CartEntry:
    """An item in the shopping cart.

    Attributes:
        product_code: Product code
        name: Product name
        brand: Brand name
        quantity: Quantity in cart
        unit_price: Price per unit
        total_price: Total price for this entry
    """

    product_code: str
    name: str
    brand: str = ""
    quantity: int = 1
    unit_price: float = 0.0
    total_price: float = 0.0

    def display_name(self) -> str:
        """Get display-friendly name with brand."""
        if self.brand:
            return f"{self.brand} - {self.name}"
        return self.name


class SuperstoreAPIClient:
    """Client for Real Canadian Superstore API.

    Provides methods for product search and cart operations using
    the pcexpress.ca BFF API.

    Usage:
        credentials = SuperstoreCredentials(cart_id="...")
        async with SuperstoreAPIClient(credentials) as client:
            results = await client.search("milk", size=5)
            await client.add_to_cart({"20657896_EA": 2})
            cart = await client.get_cart()
    """

    def __init__(
        self,
        credentials: SuperstoreCredentials | None = None,
        timeout: float = 30.0,
    ):
        """Initialize the API client.

        Args:
            credentials: Credentials for API calls. If None, a new cart
                will be created on first use.
            timeout: Request timeout in seconds
        """
        self.credentials = credentials or SuperstoreCredentials()
        self._client = httpx.AsyncClient(timeout=timeout)
        self._closed = False

    async def __aenter__(self) -> "SuperstoreAPIClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        if not self._closed:
            await self._client.aclose()
            self._closed = True

    def _get_headers(self) -> dict[str, str]:
        """Get API headers with current API key."""
        headers = API_HEADERS.copy()
        headers["x-apikey"] = self.credentials.api_key
        return headers

    async def create_cart(self, store_id: str | None = None) -> str:
        """Create a new anonymous shopping cart.

        Args:
            store_id: Store ID for pricing. Uses credentials store_id if None.

        Returns:
            New cart ID (UUID string)
        """
        store = store_id or self.credentials.store_id

        response = await self._client.post(
            f"{API_BASE_URL}/carts",
            headers=self._get_headers(),
            json={
                "bannerId": "superstore",
                "language": "en",
                "storeId": store,
            },
        )
        response.raise_for_status()
        data = response.json()

        cart_id = data.get("id")
        if cart_id:
            self.credentials.cart_id = cart_id

        return cart_id

    async def ensure_cart(self) -> str:
        """Ensure a cart exists, creating one if needed.

        Returns:
            Cart ID
        """
        if self.credentials.cart_id:
            return self.credentials.cart_id

        return await self.create_cart()

    async def search(
        self,
        term: str,
        size: int = 10,
        from_index: int = 0,
    ) -> list[ProductSearchResult]:
        """Search for products by term.

        Args:
            term: Search query (e.g., "milk", "chicken breast")
            size: Number of results to return
            from_index: Pagination offset

        Returns:
            List of matching products
        """
        # Ensure we have a cart for consistent pricing
        await self.ensure_cart()

        response = await self._client.post(
            f"{API_BASE_URL}/products/search",
            headers=self._get_headers(),
            json={
                "term": term,
                "banner": "superstore",
                "storeId": self.credentials.store_id,
                "lang": "en",
                "cartId": self.credentials.cart_id,
                "pagination": {"from": from_index, "size": size},
            },
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for product in data.get("results", []):
            prices = product.get("prices", {})
            price_info = prices.get("price", {})
            pricing_units = product.get("pricingUnits", {})

            results.append(
                ProductSearchResult(
                    code=product.get("code", ""),
                    name=product.get("name", "Unknown"),
                    brand=product.get("brand", ""),
                    price=price_info.get("value", 0.0),
                    unit=price_info.get("unit", "ea"),
                    description=product.get("description", ""),
                    min_quantity=pricing_units.get("minOrderQuantity", 1),
                    max_quantity=pricing_units.get("maxOrderQuantity", 99),
                    image_url=product.get("imageUrl"),
                    selling_type=pricing_units.get("type", "SOLD_BY_EACH"),
                )
            )

        return results

    async def typeahead(
        self,
        term: str,
        size: int = 6,
    ) -> list[str]:
        """Get autocomplete suggestions for a search term.

        Args:
            term: Partial search query
            size: Number of suggestions

        Returns:
            List of suggestion strings
        """
        await self.ensure_cart()

        response = await self._client.post(
            f"{API_BASE_URL}/products/type-ahead",
            headers=self._get_headers(),
            json={
                "banner": "superstore",
                "lang": "en",
                "storeId": self.credentials.store_id,
                "term": term,
                "cartId": self.credentials.cart_id,
                "size": size,
            },
        )
        response.raise_for_status()
        data = response.json()

        # Extract suggestion strings from response
        suggestions = []
        for item in data.get("suggestions", []):
            if isinstance(item, str):
                suggestions.append(item)
            elif isinstance(item, dict) and "term" in item:
                suggestions.append(item["term"])

        return suggestions

    async def add_to_cart(self, items: dict[str, int]) -> dict[str, Any]:
        """Add items to the cart.

        Args:
            items: Dict mapping product_code to quantity
                   e.g., {"20657896_EA": 2, "20132621001_KG": 1}

        Returns:
            Updated cart data from API

        Raises:
            ValueError: If no cart_id available
        """
        cart_id = await self.ensure_cart()

        entries = {
            code: {
                "quantity": qty,
                "fulfillmentMethod": "pickup",
                "sellerId": self.credentials.store_id,
            }
            for code, qty in items.items()
        }

        response = await self._client.post(
            f"{API_BASE_URL}/carts/{cart_id}",
            headers=self._get_headers(),
            json={"entries": entries},
        )
        response.raise_for_status()
        return response.json()

    async def get_cart(self) -> list[CartEntry]:
        """Get current cart contents.

        Returns:
            List of items in the cart
        """
        cart_id = await self.ensure_cart()

        response = await self._client.get(
            f"{API_BASE_URL}/carts/{cart_id}",
            headers=self._get_headers(),
        )
        response.raise_for_status()
        data = response.json()

        entries = []
        for order in data.get("orders", []):
            for entry in order.get("entries", []):
                offer = entry.get("offer", {})
                product = offer.get("product", {})
                prices = entry.get("prices", {})

                # Get price from prices object
                unit_price = prices.get("listPrice") or prices.get("salePrice") or 0.0
                quantity = int(entry.get("quantity", 0))

                entries.append(
                    CartEntry(
                        product_code=product.get("code", ""),
                        name=product.get("name", "Unknown"),
                        brand=product.get("brand", ""),
                        quantity=quantity,
                        unit_price=unit_price,
                        total_price=quantity * unit_price,
                    )
                )

        return entries

    async def get_cart_raw(self) -> dict[str, Any]:
        """Get raw cart data from API.

        Returns:
            Full cart JSON response
        """
        cart_id = await self.ensure_cart()

        response = await self._client.get(
            f"{API_BASE_URL}/carts/{cart_id}",
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def clear_cart(self) -> None:
        """Clear all items from the cart by creating a new one."""
        await self.create_cart()
