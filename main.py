import asyncio
import os

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv

load_dotenv()


def create_browser() -> Browser:
    return Browser(
        headless=False,  # Show browser window
        window_size={"width": 500, "height": 700},  # Set window size
        wait_between_actions=1.5,
        minimum_wait_page_load_time=1.5,
        wait_for_network_idle_page_load_time=1.5,
    )


ITEMS = ["bananas", "apples", "2% milk 1L size"]


async def run_for_item(item: str):
    print(f"Starting task for {item}...")
    browser = create_browser()
    agent = Agent(
        task=(
            f"Go to Real Canadian Superstore website, login with email "
            f"{os.getenv('SUPERSTORE_USER')} and password {os.getenv('SUPERSTORE_PASSWORD')} "
            f"and add {item} to the cart"
        ),
        llm=ChatOpenAI(model="gpt-4.1"),
        browser=browser,
    )
    await agent.run(max_steps=100)


async def main():
    await asyncio.gather(*(run_for_item(item) for item in ITEMS))


if __name__ == "__main__":
    asyncio.run(main())
