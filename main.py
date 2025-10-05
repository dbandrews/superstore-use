import asyncio

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv

load_dotenv()


def create_browser(user_data_dir: str = "./superstore-profile") -> Browser:
    return Browser(
        headless=False,  # Show browser window
        window_size={"width": 500, "height": 700},  # Set window size
        wait_between_actions=1.5,
        minimum_wait_page_load_time=1.5,
        wait_for_network_idle_page_load_time=1.5,
        user_data_dir=user_data_dir,  # Persist browser state (login, cart)
    )


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


async def add_items_to_cart(items: list[str]):
    """Adds all items to cart using a persistent browser session."""
    print(f"\n🚀 Adding {len(items)} items to cart...")

    # Create a persistent browser session
    browser = Browser(
        headless=False,
        window_size={"width": 500, "height": 700},
        wait_between_actions=1.5,
        minimum_wait_page_load_time=1.5,
        wait_for_network_idle_page_load_time=1.5,
        user_data_dir="./superstore-profile",
        keep_alive=True,  # Keep browser alive between tasks
    )

    await browser.start()

    # First task: login
    print("\n🔐 Logging in...")
    agent = Agent(
        task=(
            "Go to https://www.realcanadiansuperstore.ca website"
            # f"{os.getenv('SUPERSTORE_USER')} and password {os.getenv('SUPERSTORE_PASSWORD')}"
        ),
        llm=ChatOpenAI(model="gpt-4.1"),
        browser_session=browser,
    )
    await agent.run(max_steps=50)

    # Add each item sequentially in the same browser session
    for i, item in enumerate(items, 1):
        print(f"\n🔍 Adding item {i}/{len(items)}: {item}")
        agent.add_new_task(f"Search for {item} and add it to the cart")
        await agent.run(max_steps=50)
        print(f"✅ Added: {item}")

    print("\n✅ All items have been added to the cart!")

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
        Go to the cart and proceed through the checkout process.
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


async def main():
    """Main function that orchestrates the grocery shopping flow."""
    # Step 1: Collect items from user
    items = collect_items_from_user()

    # Step 2: Add items to cart (returns persistent browser)
    browser = await add_items_to_cart(items)

    # Step 3: Confirm and checkout
    if confirm_checkout():
        await checkout(browser)
        print("\n✅ Order completed successfully!")
    else:
        print("\n🛑 Shopping session ended without checkout.")
        await browser.kill()


if __name__ == "__main__":
    asyncio.run(main())
