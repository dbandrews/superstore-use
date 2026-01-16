"""Local CLI for Superstore shopping agent.

Provides local-only commands for login and shopping with parallel browser windows.

Usage:
    uv run -m local.cli login [--headed]     # One-time login to persist profile
    uv run -m local.cli shop [--monitor-offset N]  # Interactive shopping
"""

import argparse
import asyncio
import multiprocessing
import shutil
import tempfile
from pathlib import Path

from browser_use import Agent, ChatGroq
from dotenv import load_dotenv

from core.browser import create_browser, get_profile_dir

# Chrome lock files that should be removed when copying profiles
CHROME_LOCK_FILES = [
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
    "parent.lock",
]

MODEL_NAME = "openai/gpt-oss-120b"


def _ignore_chrome_lock_files(directory: str, files: list[str]) -> list[str]:
    """Ignore function for shutil.copytree to skip Chrome lock files."""
    return [f for f in files if f in CHROME_LOCK_FILES]


def _clean_chrome_lock_files(profile_dir: str) -> None:
    """Remove Chrome lock files from a profile directory.

    This should be called after browser is closed to prepare profile for
    copying to Modal deployment.
    """
    profile_path = Path(profile_dir)
    for lock_file in CHROME_LOCK_FILES:
        lock_path = profile_path / lock_file
        if lock_path.exists() or lock_path.is_symlink():
            try:
                lock_path.unlink()
                print(f"Cleaned lock file: {lock_file}")
            except OSError as e:
                print(f"Warning: Could not remove {lock_file}: {e}")


def copy_profile_to_temp(source_profile: Path, prefix: str = "browser-worker") -> Path:
    """Copy browser profile to a temp directory, skipping Chrome lock files.

    Args:
        source_profile: Path to the source browser profile directory
        prefix: Prefix for the temp directory name

    Returns:
        Path to the temporary profile directory
    """
    temp_dir = tempfile.mkdtemp(prefix=f"{prefix}-")
    temp_profile = Path(temp_dir) / "profile"

    if source_profile.exists():
        shutil.copytree(
            source_profile,
            temp_profile,
            ignore=_ignore_chrome_lock_files,
            dirs_exist_ok=True,
        )

    return temp_profile


load_dotenv()


# =============================================================================
# Window Positioning (for parallel browser demo)
# =============================================================================


def calculate_window_positions(
    num_windows: int,
    window_width: int = 700,
    window_height: int = 700,
    x_offset: int = 1080,
    gap: int = 20,
    y_offset: int = 50,
) -> list[tuple[int, int]]:
    """Calculate tiled positions for browser windows on the screen.

    Args:
        num_windows: Number of windows to position
        window_width: Width of each window
        window_height: Height of each window
        x_offset: Horizontal offset to shift all windows (e.g., to target a specific monitor)
        gap: Gap between windows in pixels
        y_offset: Vertical offset from top of screen (for taskbar/title bars)

    Returns:
        List of (x, y) position tuples for each window.
    """
    # Calculate optimal grid layout based on number of windows
    if num_windows == 1:
        cols = 1
    elif num_windows == 2:
        cols = 2  # Side by side
    elif num_windows <= 4:
        cols = 2  # 2x2 grid
    elif num_windows <= 6:
        cols = 3  # 2 rows of 3
    else:
        cols = 3  # 3 columns, more rows as needed

    positions = []
    for i in range(num_windows):
        row = i // cols
        col = i % cols

        # Calculate position with gap spacing between windows
        x = x_offset + col * (window_width + gap)
        y = y_offset + row * (window_height + gap)

        positions.append((x, y))

    return positions


# =============================================================================
# Login Command
# =============================================================================


async def login_and_save(headless: bool = True):
    """Log into Real Canadian Superstore and save browser state.

    Saves to ./superstore-profile/ for local development.

    Args:
        headless: Run browser in headless mode. Default True.

    Returns:
        dict with status and message
    """
    import os

    username = os.getenv("SUPERSTORE_USER")
    password = os.getenv("SUPERSTORE_PASSWORD")

    if not username or not password:
        print("Error: SUPERSTORE_USER and SUPERSTORE_PASSWORD must be set in .env")
        return {"status": "failed", "message": "Missing credentials"}

    user_data_dir, is_modal = get_profile_dir()

    print(f"Environment: {'Modal' if is_modal else 'Local'}")
    print(f"Profile: {user_data_dir}")
    print(f"Headless: {headless}")

    # Ensure profile directory exists
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    browser = create_browser(
        user_data_dir=user_data_dir,
        headless=headless,
        use_stealth=True,
        fast_mode=False,  # Use slower timing for more reliable login
    )

    try:
        agent = Agent(
            task=f"""
            Navigate to https://www.realcanadiansuperstore.ca/en and log in.

            Steps:
            1. Go to https://www.realcanadiansuperstore.ca/en
            2. If you see "My Shop" and "let's get started by shopping your regulars",
               you are already logged in - call done.
            3. Otherwise, click "Sign in" at top right.
            4. IMPORTANT: If you see an email address ({username}) displayed on the login page
               (this indicates a saved login), simply click on that email to proceed.
               Then wait patiently for the login to complete - this may take several seconds.
            5. If you don't see the email displayed, enter username: {username}
            6. Then enter password: {password}
            7. Click the sign in button.
            8. After clicking to sign in, wait patiently for as long as needed for the login
               to complete. Do not rush - the page may take several seconds to load.
            9. Wait for "My Account" at top right to confirm login.

            Complete when logged in successfully.
            """,
            llm=ChatGroq(model=MODEL_NAME),
            browser_session=browser,
            use_vision=False,
        )

        await agent.run(max_steps=50)

        print(f"Login successful! Profile saved to {user_data_dir}")
        return {"status": "success", "message": "Login successful"}

    except Exception as e:
        print(f"Login failed: {e}")
        return {"status": "failed", "message": str(e)}
    finally:
        await browser.kill()
        # Clean lock files so profile can be copied to Modal
        _clean_chrome_lock_files(user_data_dir)


def run_login(args):
    """Run the login command."""
    headless = not args.headed
    result = asyncio.run(login_and_save(headless=headless))
    if result.get("status") != "success":
        exit(1)


# =============================================================================
# Shop Command (Parallel Browser Windows)
# =============================================================================


def collect_items_from_user() -> list[str]:
    """Collects grocery items from the user through terminal conversation."""
    print("\n[Grocery Shopping Agent]")
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
            print(f"  + Added: {item}")

    print(f"\n[Shopping List] ({len(items)} items):")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}")

    return items


def add_single_item_process(args: tuple[str, int, int, tuple[int, int], str]) -> str:
    """Worker function to add a single item in a separate process.

    Args:
        args: Tuple of (item, index, total, position, temp_profile_path)

    Returns:
        String indicating success or failure: "success: {item}" or "failed: {item}"
    """
    item, index, total, position, temp_profile_path = args

    async def _add_item():
        print(f"\n[{index}/{total}] Adding: {item}")

        browser = create_browser(
            user_data_dir=temp_profile_path,
            headless=False,  # Show browser for demo
            position=position,
            window_size=(700, 700),
            use_stealth=False,  # Minimal args for local
            fast_mode=True,  # Faster timing for local
        )

        try:
            agent = Agent(
                task=f"""
                Add "{item}" to the shopping cart on Real Canadian Superstore.
                Go to https://www.realcanadiansuperstore.ca/en

                UNDERSTANDING THE ITEM REQUEST:
                The item "{item}" may include a quantity (e.g., "6 apples", "2 liters milk", "500g chicken breast").
                - Extract the product name to search for (e.g., "apples", "milk", "chicken breast")
                - Note the quantity requested (e.g., 6, 2 liters, 500g)

                Steps:
                1. Search for the PRODUCT NAME (not the full quantity string)
                   - For "6 apples", search for "apples"
                   - For "2 liters milk", search for "milk"
                2. Select the most relevant item that matches the quantity/size if possible
                3. If a specific quantity is requested (like "6 apples"):
                   - Look for a quantity selector and adjust before adding to cart
                4. Click "Add to Cart" and wait for confirmation

                Complete when the item is added to cart with the correct quantity.
                """,
                llm=ChatGroq(model=MODEL_NAME),
                browser_session=browser,
                use_vision=False,
            )
            await agent.run(max_steps=50)
            print(f"[OK] [{index}/{total}] Added: {item}")
            return f"success: {item}"
        except Exception as e:
            print(f"[FAIL] [{index}/{total}] Failed to add {item}: {e}")
            return f"failed: {item}"
        finally:
            await browser.kill()
            # Clean up temporary profile
            temp_dir = Path(temp_profile_path).parent
            shutil.rmtree(temp_dir, ignore_errors=True)

    return asyncio.run(_add_item())


async def add_items_to_cart(items: list[str], x_offset: int = 1080):
    """Adds all items to cart using parallel processes with tiled browser windows.

    Args:
        items: List of grocery items to add
        x_offset: Horizontal pixel offset for window positioning (for multi-monitor)

    Returns:
        Browser instance for checkout
    """
    print(f"\n[Starting] Adding {len(items)} items to cart in parallel...")

    # Calculate window positions for tiling (max 4 parallel processes)
    positions = calculate_window_positions(min(len(items), 4), x_offset=x_offset)

    # Pre-create temp profiles before spawning processes to avoid race conditions
    # Each worker gets its own copy of the browser profile with lock files removed
    base_profile = Path("./superstore-profile")
    print(f"[Profile] Copying browser profile for {len(items)} workers...")
    temp_profiles = [copy_profile_to_temp(base_profile, prefix=f"browser-worker-{i}") for i in range(1, len(items) + 1)]

    process_args = [
        (item, i, len(items), positions[min(i - 1, len(positions) - 1)], str(temp_profiles[i - 1]))
        for i, item in enumerate(items, 1)
    ]

    # Run items in parallel processes
    try:
        with multiprocessing.Pool(processes=min(len(items), 4)) as pool:
            results = pool.map(add_single_item_process, process_args)
    except KeyboardInterrupt:
        print("\n[Interrupted] Cleaning up temp profiles...")
        results = []
    finally:
        # Clean up any remaining temp profiles (workers should clean their own,
        # but this catches any that weren't cleaned due to interruption)
        for temp_profile in temp_profiles:
            temp_dir = temp_profile.parent
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    # Report results
    successes = [r for r in results if r.startswith("success")]
    failures = [r for r in results if r.startswith("failed")]

    print(f"\n[Result] Added {len(successes)}/{len(items)} items to cart")
    if failures:
        print(f"[Warning] Failed items: {', '.join(f.replace('failed: ', '') for f in failures)}")

    # Create a fresh browser for checkout
    browser = create_browser(
        headless=False,
        window_size=(700, 700),
        use_stealth=False,
        fast_mode=True,
    )

    return browser


def confirm_place_order() -> bool:
    """Final confirmation before placing the order."""
    print("\n" + "=" * 50)
    print("[FINAL CONFIRMATION - PLACE ORDER]")
    print("=" * 50)
    print("You are about to place the order!")
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


async def checkout(browser):
    """Performs checkout using the existing browser session with items in cart."""
    print("\n[Checkout] Starting checkout process...")

    # Step 1: Navigate to cart and proceed to checkout page (but don't place order yet)
    agent = Agent(
        task="""
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
        """,
        llm=ChatGroq(model=MODEL_NAME),
        browser_session=browser,
        use_vision=False,
    )
    await agent.run(max_steps=100)

    # Step 2: Ask for final confirmation from user
    if confirm_place_order():
        print("\n[Checkout] Placing order...")
        # Step 3: Complete the order
        agent.add_new_task("Click the 'Place Order' or 'Submit Order' button to complete the purchase.")
        await agent.run(max_steps=10)
        print("\n[Success] Order has been placed!")
    else:
        print("\n[Cancelled] Order was not placed.")

    # Close browser after checkout
    await browser.kill()


def confirm_checkout() -> bool:
    """Asks user to confirm before proceeding to checkout."""
    print("\n" + "=" * 50)
    print("[Ready to proceed to checkout]")
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


async def shop_main(x_offset: int = 1080):
    """Main function that orchestrates the grocery shopping flow."""
    # Step 1: Collect items from user
    items = collect_items_from_user()

    # Step 2: Add items to cart (returns persistent browser)
    browser = await add_items_to_cart(items, x_offset=x_offset)

    # Step 3: Confirm and checkout
    if confirm_checkout():
        await checkout(browser)
        print("\n[Done] Order completed successfully!")
    else:
        print("\n[Done] Shopping session ended without checkout.")
        await browser.kill()


def run_shop(args):
    """Run the shop command."""
    asyncio.run(shop_main(x_offset=args.monitor_offset))


# =============================================================================
# Main Entry Point
# =============================================================================


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(description="AI-powered grocery shopping agent for Real Canadian Superstore")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Login command
    login_parser = subparsers.add_parser("login", help="Log in and save browser profile")
    login_parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible window)",
    )
    login_parser.set_defaults(func=run_login)

    # Shop command
    shop_parser = subparsers.add_parser("shop", help="Interactive shopping with parallel browsers")
    shop_parser.add_argument(
        "--monitor-offset",
        type=int,
        default=1080,
        help="Horizontal pixel offset for window positioning (default: 1080, for secondary monitor)",
    )
    shop_parser.set_defaults(func=run_shop)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
