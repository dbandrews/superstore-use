"""CLI for API-based grocery shopping agent.

Provides a fast, API-based shopping experience without browser automation.
Uses the Superstore API directly for search and cart operations.

Usage:
    uv run -m src.api.cli              # Start interactive shopping (anonymous cart)
    uv run -m src.api.cli --profile    # Use saved browser profile for credentials
    uv run -m src.api.cli --test       # Quick API test (no interaction)
"""

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from src.api.agent import create_api_agent
from src.api.tools import initialize_anonymous_session, set_credentials
from src.api.credentials import (
    SuperstoreCredentials,
    extract_credentials_from_profile,
    DEFAULT_STORE_ID,
)
from src.api.client import SuperstoreAPIClient

load_dotenv()


async def run_interactive(credentials: SuperstoreCredentials):
    """Run the interactive chat agent."""
    from langchain_core.messages import AIMessage, HumanMessage

    print("\n[API-Based Grocery Shopping Agent]")
    print("=" * 50)
    print(f"Cart ID: {credentials.cart_id[:8]}..." if credentials.cart_id else "No cart")
    print(f"Store ID: {credentials.store_id}")
    print()
    print("I can help you shop for groceries at Real Canadian Superstore.")
    print("This uses direct API calls - much faster than browser automation!")
    print()
    print("Try commands like:")
    print("  - 'search for milk'")
    print("  - 'find chicken breast'")
    print("  - 'show my cart'")
    print()
    print("Type 'quit' to exit.\n")

    agent = create_api_agent(credentials)
    config = {"configurable": {"thread_id": "cli-session-1"}}

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "bye", "q"]:
                print("Goodbye!")
                break

            # Invoke the agent
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
            )

            # Print the assistant's response
            last_message = result["messages"][-1]
            if isinstance(last_message, AIMessage):
                print(f"\nAssistant: {last_message.content}\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


async def run_test():
    """Quick API test without interaction."""
    print("\n[API Test Mode]")
    print("=" * 50)

    # Create anonymous session
    print("Creating anonymous cart...")
    credentials = await initialize_anonymous_session()
    print(f"Cart ID: {credentials.cart_id}")
    print(f"Store ID: {credentials.store_id}")

    # Test search
    print("\nSearching for 'milk'...")
    async with SuperstoreAPIClient(credentials) as client:
        results = await client.search("milk", size=3)
        print(f"Found {len(results)} products:")
        for i, product in enumerate(results, 1):
            print(f"  {i}. {product.display_name()} - ${product.price:.2f}")
            print(f"     Code: {product.code}")

        # Test add to cart (using first result)
        if results:
            print(f"\nAdding '{results[0].name}' to cart...")
            await client.add_to_cart({results[0].code: 1})
            print("Added!")

            # View cart
            print("\nCart contents:")
            cart = await client.get_cart()
            for entry in cart:
                print(f"  - {entry.quantity}x {entry.display_name()} = ${entry.total_price:.2f}")

    print("\n[Test Complete]")


async def main_async(args):
    """Async main function."""
    if args.test:
        await run_test()
        return

    # Determine how to get credentials
    if args.profile:
        # Extract from browser profile
        profile_path = args.profile
        if not Path(profile_path).exists():
            print(f"Error: Profile directory not found: {profile_path}")
            print("Run 'uv run -m src.local.cli login' first to create a profile.")
            exit(1)

        print(f"Extracting credentials from profile: {profile_path}")
        credentials = await extract_credentials_from_profile(profile_path, headless=True)

        if not credentials.cart_id:
            print("Warning: No cart found in profile. Creating anonymous cart.")
            credentials = await initialize_anonymous_session(credentials.store_id)
    else:
        # Create anonymous session
        print("Creating anonymous shopping session...")
        credentials = await initialize_anonymous_session(
            store_id=args.store or DEFAULT_STORE_ID
        )

    # Set credentials for tools
    set_credentials(credentials)

    # Run interactive mode
    await run_interactive(credentials)


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="API-based grocery shopping agent (fast, no browser automation)"
    )

    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Path to browser profile directory for credentials (default: anonymous cart)",
    )

    parser.add_argument(
        "--store",
        type=str,
        default=None,
        help=f"Store ID for pricing (default: {DEFAULT_STORE_ID} - Calgary)",
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Run quick API test without interaction",
    )

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
