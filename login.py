import asyncio
import os

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv

load_dotenv()


async def login_and_save():
    """Logs into Real Canadian Superstore and saves browser state."""
    username = os.getenv("SUPERSTORE_USER")
    password = os.getenv("SUPERSTORE_PASSWORD")

    if not username or not password:
        print("❌ Error: SUPERSTORE_USER and SUPERSTORE_PASSWORD must be set in .env file")
        return

    print("🌐 Starting browser and logging in to Real Canadian Superstore...")
    print(f"   Username: {username}")

    # Create browser with persistent profile
    browser = Browser(
        headless=False,
        window_size={"width": 1200, "height": 900},
        wait_between_actions=1.5,
        minimum_wait_page_load_time=1.5,
        wait_for_network_idle_page_load_time=1.5,
        user_data_dir="./superstore-profile",  # Save browser state here
    )

    try:
        agent = Agent(
            task=f"""
            Navigate to https://www.realcanadiansuperstore.ca/ and log in.

            Steps:
            1. Go to the website
            2. Click on the sign in or account button (usually in top right)
            3. Enter username: {username}
            4. Enter password: {password}
            5. Click the login/sign in button
            6. Wait for successful login (you should see account menu or user name displayed)

            Complete the task once you're fully logged in.
            """,
            llm=ChatOpenAI(model="gpt-4.1"),
            browser_session=browser,
        )

        await agent.run(max_steps=50)

        print("\n✅ Login successful! Browser state saved to ./superstore-profile/")
        print("   You can now use this profile in other scripts to stay logged in.")

    except Exception as e:
        print(f"\n❌ Login failed: {e}")
    finally:
        # Browser state is automatically saved when closing
        await browser.kill()


if __name__ == "__main__":
    asyncio.run(login_and_save())
