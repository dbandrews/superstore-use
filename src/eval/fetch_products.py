"""Fetch products from Real Canadian Superstore.

Uses Playwright to extract product data from the website DOM.
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def fetch_products(
    output_file: str = "products.json",
    max_searches: int = 50,
    headless: bool = True,
) -> list[dict]:
    """Fetch products by searching and extracting from DOM.

    Args:
        output_file: Output JSON file path
        max_searches: Maximum number of search terms to process
        headless: Run browser in headless mode

    Returns:
        List of extracted products
    """
    all_products = []
    seen_codes = set()

    search_terms = [
        # Generic products
        "milk", "bread", "eggs", "chicken", "beef", "pork", "fish", "salmon",
        "apple", "banana", "orange", "grape", "strawberry", "blueberry", "mango",
        "tomato", "potato", "onion", "carrot", "lettuce", "broccoli", "cucumber",
        "cheese", "yogurt", "butter", "cream", "sour cream",
        "rice", "pasta", "noodles", "cereal", "oatmeal",
        "coffee", "tea", "juice", "water", "pop", "soda",
        "chips", "crackers", "cookies", "chocolate", "candy",
        "frozen pizza", "ice cream", "frozen vegetables",
        "soup", "sauce", "ketchup", "mustard", "mayonnaise",
        "oil", "vinegar", "sugar", "flour", "salt",
        "soap", "shampoo", "toothpaste",
        "laundry detergent", "dish soap", "paper towel",
        "baby food", "diapers",
        "dog food", "cat food",
        # Brand names
        "kraft", "heinz", "kellogg", "quaker", "nestle", "campbell",
        "tropicana", "dole", "pillsbury", "betty crocker",
        "president choice", "no name brand", "compliments",
        "lays", "doritos", "oreo", "ritz", "triscuit",
        "tide", "bounty", "charmin", "pampers", "huggies",
        "coca cola", "pepsi", "gatorade", "red bull",
        "danone", "yoplait", "activia", "oikos",
        "hellmann", "philadelphia cream cheese", "black diamond",
        "natrel", "beatrice", "sealtest", "lactantia",
        "wonder bread", "dempster", "country harvest",
        "maple leaf", "schneider", "lilydale",
    ]

    print("Starting product fetch...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        for i, term in enumerate(search_terms[:max_searches]):
            url = f"https://www.realcanadiansuperstore.ca/search?search-bar={term}"
            print(f"\n[{i+1}/{min(len(search_terms), max_searches)}] Searching: {term}")

            try:
                await page.goto(url, timeout=60000)
                await asyncio.sleep(4)

                # Scroll to load more products
                for _ in range(5):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(1)

                # Extract products from links
                products = await page.evaluate(
                    """
                    () => {
                        const products = [];
                        const links = document.querySelectorAll('a[href*="/p/"]');
                        const seen = new Set();

                        for (const link of links) {
                            const href = link.href;
                            const match = href.match(/\\/p\\/([^/?]+)/);
                            if (!match) continue;

                            const code = match[1];
                            if (seen.has(code)) continue;
                            seen.add(code);

                            const tile = link.closest('[data-testid]') || link.parentElement;
                            const product = { code: code };

                            // Get name
                            const nameEl = tile ? tile.querySelector('h3, h4, [class*="title"]') : null;
                            if (nameEl) product.name = nameEl.textContent.trim();

                            // Get price text
                            const priceEl = tile ? tile.querySelector('[class*="price"]') : null;
                            if (priceEl) product.priceText = priceEl.textContent.trim();

                            // Get image
                            const imgEl = tile ? tile.querySelector('img') : null;
                            if (imgEl && imgEl.src) product.imageUrl = imgEl.src;

                            products.push(product);
                        }

                        return products;
                    }
                    """
                )

                new_count = 0
                for product in products:
                    code = product.get("code")
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        product["_search_term"] = term
                        all_products.append(product)
                        new_count += 1

                print(f"  Found {new_count} new products (total: {len(all_products)})")

            except Exception as e:
                print(f"  Error: {e}")

            # Save progress periodically
            if all_products and (i + 1) % 5 == 0:
                with open(output_file, "w") as f:
                    json.dump(all_products, f, indent=2)
                print(f"  Progress saved: {len(all_products)} products")

        await browser.close()

    # Final save
    print(f"\n{'='*50}")
    print(f"Total unique products: {len(all_products)}")

    with open(output_file, "w") as f:
        json.dump(all_products, f, indent=2)
    print(f"Saved to {output_file}")

    return all_products


async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Fetch products from Superstore")
    parser.add_argument("-o", "--output", default="products.json", help="Output JSON file")
    parser.add_argument("--max-searches", type=int, default=50, help="Max search terms")
    parser.add_argument("--headed", action="store_true", help="Show browser")

    args = parser.parse_args()

    await fetch_products(
        args.output,
        args.max_searches,
        headless=not args.headed,
    )


if __name__ == "__main__":
    asyncio.run(main())
