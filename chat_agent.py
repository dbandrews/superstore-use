"""
Chat-based Meal Planning Agent for Real Canadian Superstore.

Uses the Superstore API directly for fast product search and cart operations.
LangGraph powers the conversational agent that helps users plan meals
and add ingredients to their cart.

Usage:
    uv run chat_agent.py
"""

import os
from typing import Literal

import requests
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

load_dotenv()


# =============================================================================
# Superstore API Client
# =============================================================================


class SuperstoreAPI:
    """Client for the Real Canadian Superstore API."""

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
        "accept": "application/json",
    }

    def __init__(self, store_id: str = "1545"):
        self.store_id = store_id
        self.cart_id: str | None = None

    def create_cart(self) -> str:
        """Create a new shopping cart and return the cart ID."""
        resp = requests.post(
            f"{self.BASE_URL}/carts",
            headers=self.HEADERS,
            json={
                "bannerId": "superstore",
                "language": "en",
                "storeId": self.store_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.cart_id = data["id"]
        return self.cart_id

    def get_cart(self) -> dict:
        """Get the current cart contents."""
        if not self.cart_id:
            raise ValueError("No cart created. Call create_cart() first.")
        resp = requests.get(
            f"{self.BASE_URL}/carts/{self.cart_id}",
            headers=self.HEADERS,
        )
        resp.raise_for_status()
        return resp.json()

    def search(self, term: str, size: int = 10) -> list[dict]:
        """Search for products by term."""
        resp = requests.post(
            f"{self.BASE_URL}/products/search",
            headers=self.HEADERS,
            json={
                "term": term,
                "banner": "superstore",
                "storeId": self.store_id,
                "lang": "en",
                "cartId": self.cart_id,
                "pagination": {"from": 0, "size": size},
            },
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def add_to_cart(self, items: dict[str, int]) -> dict:
        """
        Add items to cart.

        Args:
            items: Dict of {product_code: quantity}
                   e.g., {"20028593001_EA": 3, "20132621001_KG": 2}
        """
        if not self.cart_id:
            raise ValueError("No cart created. Call create_cart() first.")

        entries = {
            code: {
                "quantity": qty,
                "fulfillmentMethod": "pickup",
                "sellerId": self.store_id,
            }
            for code, qty in items.items()
        }

        resp = requests.post(
            f"{self.BASE_URL}/carts/{self.cart_id}",
            headers=self.HEADERS,
            json={"entries": entries},
        )
        resp.raise_for_status()
        return resp.json()


# Global API client instance
api = SuperstoreAPI()


# =============================================================================
# LangGraph Tools
# =============================================================================


class SearchResult(BaseModel):
    """A product search result."""

    code: str
    name: str
    brand: str
    price: float
    unit: str


@tool
def search_products(query: str) -> str:
    """
    Search for grocery products at Real Canadian Superstore.

    Args:
        query: Search term (e.g., "milk", "eggs", "chicken breast")

    Returns:
        List of matching products with codes, names, brands, and prices.
    """
    try:
        results = api.search(query, size=5)
        if not results:
            return f"No products found for '{query}'."

        output = f"Found {len(results)} products for '{query}':\n\n"
        for i, product in enumerate(results, 1):
            name = product.get("name", "Unknown")
            brand = product.get("brand", "")
            code = product.get("code", "")
            prices = product.get("prices", {})
            price_info = prices.get("price", {})
            price = price_info.get("value", 0)
            unit = price_info.get("unit", "ea")

            output += f"{i}. {name}"
            if brand:
                output += f" ({brand})"
            output += f"\n   Code: {code}\n   Price: ${price:.2f}/{unit}\n\n"

        return output
    except Exception as e:
        return f"Error searching for products: {e}"


@tool
def add_item_to_cart(product_code: str, quantity: int = 1) -> str:
    """
    Add a product to the shopping cart.

    Args:
        product_code: The product code (e.g., "20028593001_EA")
        quantity: Number of items to add (default 1)

    Returns:
        Confirmation message.
    """
    try:
        api.add_to_cart({product_code: quantity})
        return f"Added {quantity}x {product_code} to cart."
    except Exception as e:
        return f"Error adding to cart: {e}"


@tool
def view_cart() -> str:
    """
    View the current shopping cart contents.

    Returns:
        List of items in cart with quantities and prices.
    """
    try:
        cart = api.get_cart()
        orders = cart.get("orders", [])
        if not orders:
            return "Your cart is empty."

        output = "Current cart contents:\n\n"
        total = 0.0

        for order in orders:
            entries = order.get("entries", [])
            for entry in entries:
                # Product info is nested in offer.product
                offer = entry.get("offer", {})
                product = offer.get("product", {})
                name = product.get("name", "Unknown item")
                qty = int(entry.get("quantity", 0))
                unit_price = offer.get("regularPrice", 0)
                line_total = qty * unit_price
                total += line_total
                output += f"- {name} x{qty}: ${line_total:.2f}\n"

        output += f"\nEstimated Total: ${total:.2f}"
        return output
    except Exception as e:
        return f"Error viewing cart: {e}"


@tool
def create_new_cart() -> str:
    """
    Create a new shopping cart. Call this at the start of a shopping session.

    Returns:
        Confirmation with cart ID.
    """
    try:
        cart_id = api.create_cart()
        return f"Created new cart (ID: {cart_id[:8]}...)"
    except Exception as e:
        return f"Error creating cart: {e}"


# =============================================================================
# LangGraph Agent
# =============================================================================

tools = [search_products, add_item_to_cart, view_cart, create_new_cart]
llm = ChatOpenAI(model="gpt-4o")
llm_with_tools = llm.bind_tools(tools)

SYSTEM_PROMPT = """You are a friendly meal planning assistant for Real Canadian Superstore.

Your job is to help users:
1. Brainstorm meal ideas based on their preferences, dietary needs, or what they're craving
2. Suggest ingredients needed for meals
3. Search for products and add them to their cart

## Workflow

1. **Start by creating a cart**: Always call create_new_cart at the beginning of a session.

2. **Discuss meal ideas**: Ask about dietary preferences, what cuisines they like, how many people they're cooking for, etc.

3. **Suggest meals**: Based on the conversation, suggest specific meals with their ingredients.

4. **Search and add**: When the user agrees to a meal:
   - Search for each ingredient using search_products
   - Show them the options found
   - Add their preferred items using add_item_to_cart (use the product code from search results)

5. **Review cart**: Use view_cart to show what's been added.

## Guidelines

- Be conversational and helpful - this is a meal planning chat, not just a shopping list
- Ask clarifying questions: "Are you looking for something quick and easy, or more elaborate?"
- Suggest complete meals, not just ingredients
- When adding items, search first to find the exact product code
- Confirm before adding expensive items
- If a product isn't found, suggest alternatives

## Example Conversation

User: "I want to make something Italian for dinner"
Assistant: "Italian sounds delicious! Are you in the mood for pasta, risotto, or something like chicken parmesan? And how many people are you cooking for?"

User: "Pasta for 2, something creamy"
Assistant: "Perfect! How about Fettuccine Alfredo? It's creamy, comforting, and quick to make. You'd need:
- Fettuccine pasta
- Heavy cream
- Parmesan cheese
- Butter
- Garlic

Or if you want something with protein, Carbonara adds pancetta/bacon and eggs.

Which sounds better?"

User: "Carbonara please"
Assistant: [Searches for each ingredient, shows options, adds to cart based on user preferences]
"""


class GroceryState(MessagesState):
    """State for the grocery shopping agent."""

    pass


def chat_node(state: GroceryState):
    """Main chat node that processes user messages."""
    messages = state["messages"]

    # Add system prompt if not present
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)

    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


def should_continue(state: GroceryState) -> Literal["tools", "__end__"]:
    """Determine if we should continue to tools or end."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "__end__"


def create_meal_agent():
    """Create and return the meal planning agent graph."""
    workflow = StateGraph(GroceryState)

    workflow.add_node("chat", chat_node)
    workflow.add_node("tools", ToolNode(tools))

    workflow.add_edge(START, "chat")
    workflow.add_conditional_edges("chat", should_continue)
    workflow.add_edge("tools", "chat")

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


# =============================================================================
# CLI Interface
# =============================================================================


def run_cli():
    """Run the agent in CLI mode."""
    agent = create_meal_agent()
    config = {"configurable": {"thread_id": "meal-planning-session-1"}}

    print("\n" + "=" * 60)
    print("  Meal Planning Agent - Real Canadian Superstore")
    print("=" * 60)
    print("\nHi! I'm your meal planning assistant.")
    print("Tell me what you're in the mood for, and I'll help you")
    print("plan a meal and add ingredients to your cart.")
    print("\nType 'quit' to exit, 'cart' to view your cart.\n")

    # Start with a greeting that triggers cart creation
    result = agent.invoke(
        {"messages": [HumanMessage(content="Hello, I'd like to plan a meal")]},
        config=config,
    )

    # Print initial response
    for msg in result["messages"]:
        if isinstance(msg, AIMessage) and msg.content:
            print(f"Assistant: {msg.content}\n")
            break

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "bye"]:
                print("\nGoodbye! Enjoy your meal!\n")
                break
            if user_input.lower() == "cart":
                user_input = "Show me what's in my cart"

            result = agent.invoke(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
            )

            # Print the assistant's response
            last_message = result["messages"][-1]
            if isinstance(last_message, AIMessage):
                if last_message.content:
                    print(f"\nAssistant: {last_message.content}\n")

        except KeyboardInterrupt:
            print("\n\nGoodbye! Enjoy your meal!\n")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    run_cli()
