import argparse
import asyncio
import multiprocessing
import shutil
import tempfile
from pathlib import Path

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv

load_dotenv()


def create_browser(user_data_dir: str = "./superstore-profile", position: tuple[int, int] | None = None) -> Browser:
    browser_kwargs = {
        "headless": False,  # Show browser window
        "window_size": {"width": 700, "height": 700},  # Set window size
        "wait_between_actions": 0.1,
        "minimum_wait_page_load_time": 0.1,
        "wait_for_network_idle_page_load_time": 1.5,
        "user_data_dir": user_data_dir,  # Persist browser state (login, cart)
        "args": ["--disable-features=LockProfileCookieDatabase"],
    }

    # Add window position if specified
    if position:
        x, y = position
        # Note: browser-use uses 'width' for x-position and 'height' for y-position
        browser_kwargs["window_position"] = {"width": x, "height": y}

    return Browser(**browser_kwargs)


def calculate_window_positions(
    num_windows: int, window_width: int = 700, window_height: int = 700, x_offset: int = 1080
) -> list[tuple[int, int]]:
    """Calculate tiled positions for browser windows on the screen.

    Args:
        num_windows: Number of windows to position
        window_width: Width of each window
        window_height: Height of each window
        x_offset: Horizontal offset to shift all windows (e.g., to target a specific monitor)
    """
    # Calculate grid dimensions (prefer horizontal tiling)
    if num_windows <= 2:
        cols = num_windows
    elif num_windows <= 4:
        cols = 2
    elif num_windows <= 6:
        cols = 3
    else:
        cols = 3

    positions = []
    for i in range(num_windows):
        row = i // cols
        col = i % cols

        # Calculate position with some spacing
        x = col * window_width
        y = row * window_height

        # Add offset to target specific monitor
        x += x_offset

        # Add small offset to avoid exact overlap with taskbar/system UI
        y += 10

        positions.append((x, y))

    return positions


def collect_items_from_user() -> list[str]:
    """Collects grocery items from the user through terminal conversation."""
    print("\n🛒 Grocery Shopping Agent")
    print("=" * 50)
    print("Welcome! I'll help you order groceries from Real Canadian Superstore.")
    print("\nPlease enter the items you'd like to add to your cart.")
    print("(Enter each item on a new line, type 'done' when finished)\n")

    items = []
    while True:
        item = input(f"Item #{len(items) + 1} (or 'done'): ").strip()
        if item.lower() == "done":
            if items:
                break
            else:
                print("Please add at least one item.")
                continue
        if item:
            items.append(item)
            print(f"  ✓ Added: {item}")

    print(f"\n📝 Your shopping list ({len(items)} items):")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}")

    return items


def add_single_item_process(args: tuple[str, int, int, tuple[int, int]]) -> str:
    """Worker function to add a single item in a separate process."""
    item, index, total, position = args

    async def _add_item():
        print(f"\n🔍 [{index}/{total}] Adding: {item}")

        # Create temporary profile copy for this worker to avoid conflicts
        base_profile = Path("./superstore-profile")
        temp_dir = tempfile.mkdtemp(prefix=f"browser-worker-{index}-")
        temp_profile = Path(temp_dir) / "profile"

        # Copy profile if it exists
        if base_profile.exists():
            shutil.copytree(base_profile, temp_profile, dirs_exist_ok=True)

        browser = create_browser(user_data_dir=str(temp_profile), position=position)

        try:
            agent = Agent(
                task=f"Go to https://www.realcanadiansuperstore.ca, search for {item} and add it to the cart",
                llm=ChatOpenAI(model="gpt-4.1"),
                browser_session=browser,
            )
            await agent.run(max_steps=50)
            print(f"✅ [{index}/{total}] Added: {item}")
            return f"success: {item}"
        except Exception as e:
            print(f"❌ [{index}/{total}] Failed to add {item}: {e}")
            return f"failed: {item}"
        finally:
            await browser.kill()
            # Clean up temporary profile
            shutil.rmtree(temp_dir, ignore_errors=True)

    return asyncio.run(_add_item())


async def add_items_to_cart(items: list[str], x_offset: int = 1080):
    """Adds all items to cart using parallel processes."""
    print(f"\n🚀 Adding {len(items)} items to cart in parallel...")

    # Calculate window positions for tiling
    positions = calculate_window_positions(min(len(items), 4), x_offset=x_offset)  # Max 4 parallel processes

    process_args = [(item, i, len(items), positions[min(i - 1, len(positions) - 1)]) for i, item in enumerate(items, 1)]

    # Run items in parallel processes
    with multiprocessing.Pool(processes=min(len(items), 4)) as pool:
        results = pool.map(add_single_item_process, process_args)

    # Report results
    successes = [r for r in results if r.startswith("success")]
    failures = [r for r in results if r.startswith("failed")]

    print(f"\n✅ Added {len(successes)}/{len(items)} items to cart")
    if failures:
        print(f"⚠️  Failed items: {', '.join(f.replace('failed: ', '') for f in failures)}")

    # Create a fresh browser for checkout
    browser = create_browser()

    return browser  # Return browser for checkout


def confirm_place_order() -> bool:
    """Final confirmation before placing the order."""
    print("\n" + "=" * 50)
    print("🛒 FINAL CONFIRMATION - PLACE ORDER")
    print("=" * 50)
    print("⚠️  You are about to place the order!")
    print("Please review the order details in the browser window.")
    print("=" * 50)
    while True:
        response = input("Confirm and place order? (yes/no): ").strip().lower()
        if response in ["yes", "y"]:
            return True
        elif response in ["no", "n"]:
            print("Order cancelled.")
            return False
        else:
            print("Please enter 'yes' or 'no'")


async def checkout(browser: Browser):
    """Performs checkout using the existing browser session with items in cart."""
    print("\n💳 Starting checkout process...")

    # Step 1: Navigate to cart and proceed to checkout page (but don't place order yet)
    agent = Agent(
        task=(
            """
        Go to the https://www.realcanadiansuperstore.ca/ and proceed through the checkout process.
        The cart option should be at top right of the main page.

        Navigate through all checkout steps:
        - Delivery details: Click "select a time" and pick the next available time slot.
        - Item details
        - Contact details
        - Driver tip
        - Payment

        Each step will need interaction and need to hit "Save & Continue" after each one.
        Stop when you reach the final order review page where the "Place Order" button is visible and a dark green color.
        """
        ),
        llm=ChatOpenAI(model="gpt-4.1"),
        browser_session=browser,
    )
    await agent.run(max_steps=100)

    # Step 2: Ask for final confirmation from user
    if confirm_place_order():
        print("\n✅ Placing order...")
        # Step 3: Complete the order
        agent.add_new_task("Click the 'Place Order' or 'Submit Order' button to complete the purchase.")
        await agent.run(max_steps=10)
        print("\n🎉 Order has been placed!")
    else:
        print("\n🛑 Order was not placed.")

    # Close browser after checkout
    await browser.kill()


def confirm_checkout() -> bool:
    """Asks user to confirm before proceeding to checkout."""
    print("\n" + "=" * 50)
    print("⚠️  Ready to proceed to checkout")
    print("=" * 50)
    while True:
        response = input("Do you want to proceed with checkout? (yes/no): ").strip().lower()
        if response in ["yes", "y"]:
            return True
        elif response in ["no", "n"]:
            print("Checkout cancelled.")
            return False
        else:
            print("Please enter 'yes' or 'no'")


async def main(x_offset: int = 1080):
    """Main function that orchestrates the grocery shopping flow."""
    # Step 1: Collect items from user
    items = collect_items_from_user()

    # Step 2: Add items to cart (returns persistent browser)
    browser = await add_items_to_cart(items, x_offset=x_offset)

    # Step 3: Confirm and checkout
    if confirm_checkout():
        await checkout(browser)
        print("\n✅ Order completed successfully!")
    else:
        print("\n🛑 Shopping session ended without checkout.")
        await browser.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI-powered grocery shopping agent for Real Canadian Superstore")
    parser.add_argument(
        "--monitor-offset",
        type=int,
        default=1080,
        help="Horizontal pixel offset for positioning windows (default: 1080, for secondary monitor)",
    )
    args = parser.parse_args()

    asyncio.run(main(x_offset=args.monitor_offset))
