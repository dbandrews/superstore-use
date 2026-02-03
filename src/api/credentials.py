"""Credential extraction and storage for Superstore API.

Handles extracting API credentials from browser session after login
and storing them for use by API-based tools.

The Real Canadian Superstore API uses:
- Static API Key: C1xujSegT5j3ap3yexJjqhOfELwGKYvz (public, embedded in frontend)
- Cart ID: Anonymous cart stored in localStorage as ANONYMOUS_CART_ID
- Store ID: Required for pricing/availability (e.g., 1545 for Calgary)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


# Public API key embedded in the frontend
DEFAULT_API_KEY = "C1xujSegT5j3ap3yexJjqhOfELwGKYvz"

# Default store ID (Calgary location)
DEFAULT_STORE_ID = "1545"


@dataclass
class SuperstoreCredentials:
    """Credentials needed for Superstore API calls.

    Attributes:
        api_key: Static public API key for pcexpress.ca
        cart_id: Anonymous cart ID from localStorage
        store_id: Store ID for pricing/availability
    """

    api_key: str = DEFAULT_API_KEY
    cart_id: str | None = None
    store_id: str = DEFAULT_STORE_ID

    def is_valid(self) -> bool:
        """Check if credentials are sufficient for API calls."""
        return self.cart_id is not None

    def to_dict(self) -> dict:
        """Convert credentials to dictionary for storage."""
        return {
            "api_key": self.api_key,
            "cart_id": self.cart_id,
            "store_id": self.store_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SuperstoreCredentials":
        """Create credentials from dictionary."""
        return cls(
            api_key=data.get("api_key", DEFAULT_API_KEY),
            cart_id=data.get("cart_id"),
            store_id=data.get("store_id", DEFAULT_STORE_ID),
        )


async def extract_credentials_from_page(page: "Page") -> SuperstoreCredentials:
    """Extract API credentials from a logged-in browser page.

    Extracts the cart ID and store ID from localStorage after the user
    has logged in or visited the site.

    Args:
        page: Playwright page with active session

    Returns:
        SuperstoreCredentials with extracted values
    """
    creds = SuperstoreCredentials()

    # Extract cart ID from localStorage
    cart_id = await page.evaluate("() => localStorage.getItem('ANONYMOUS_CART_ID')")
    if cart_id:
        creds.cart_id = cart_id

    # Try to extract store ID from localStorage
    # The site stores this in various keys
    store_id = await page.evaluate(
        """() => {
            // Try multiple possible localStorage keys
            return localStorage.getItem('selectedStoreId')
                || localStorage.getItem('lcl-selected-store')
                || localStorage.getItem('storeId')
                || null;
        }"""
    )
    if store_id:
        creds.store_id = store_id

    return creds


async def extract_credentials_from_profile(
    profile_path: str,
    headless: bool = True,
) -> SuperstoreCredentials:
    """Extract credentials from a saved browser profile.

    Launches a browser with the saved profile to extract localStorage values.

    Args:
        profile_path: Path to the browser profile directory
        headless: Run browser in headless mode

    Returns:
        SuperstoreCredentials with extracted values
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=headless,
            args=["--disable-features=LockProfileCookieDatabase"],
        )

        try:
            page = await browser.new_page()

            # Navigate to the site to access localStorage
            await page.goto(
                "https://www.realcanadiansuperstore.ca",
                wait_until="domcontentloaded",
                timeout=30000,
            )

            # Wait for localStorage to be populated
            import asyncio

            await asyncio.sleep(2)

            return await extract_credentials_from_page(page)

        finally:
            await browser.close()
