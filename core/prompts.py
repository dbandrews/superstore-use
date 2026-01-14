"""
Centralized prompt templates for browser-use agents.

All agent task prompts are defined here for consistency across:
- Local CLI (local/cli.py)
- Modal deployment (modal_app.py)
- Evaluation script (eval.py)

Prompts can be customized via Hydra configuration in conf/config.yaml.
"""

from typing import Any

from omegaconf import DictConfig, OmegaConf

# =============================================================================
# Default Prompt Templates
# =============================================================================

# Login prompt template
LOGIN_PROMPT_TEMPLATE = """Navigate to https://www.realcanadiansuperstore.ca/en and log in.

Steps:
1. Go to https://www.realcanadiansuperstore.ca/en
2. If you see "My Shop" and "let's get started by shopping your regulars",
   you are already logged in - call done.
3. Otherwise, click "Sign in" at top right.
4. Enter username: {username}
5. Enter password: {password}
6. Click the sign in button.
7. Wait for "My Account" at top right to confirm login.

Complete when logged in."""

# Add item to cart prompt template
ADD_ITEM_PROMPT_TEMPLATE = """Add "{item}" to the shopping cart on Real Canadian Superstore.
Go to https://www.realcanadiansuperstore.ca/en

UNDERSTANDING THE ITEM REQUEST:
The item "{item}" may include a quantity (e.g., "6 apples", "2 liters milk", "500g chicken breast").
- Extract the product name to search for (e.g., "apples", "milk", "chicken breast")
- Note the quantity requested (e.g., 6, 2 liters, 500g)

Steps:
1. Use the search bar to search for the PRODUCT NAME (not the full quantity string)
   - For "6 apples", search for "apples"
   - For "2 liters milk", search for "milk"
   - For "500g chicken breast", search for "chicken breast"
2. From the search results, select the most relevant item that matches the quantity/size if possible
   - If looking for "2 liters milk", prefer 2L milk containers
   - If looking for "500g chicken", prefer ~500g packages
3. If a specific quantity is requested (like "6 apples"):
   - Look for a quantity selector/input field on the product
   - Adjust the quantity before adding to cart
   - If no quantity selector, you may need to click "Add to Cart" multiple times
4. Click "Add to Cart" or similar button
5. Wait for confirmation that item was added (look for cart update or confirmation message)

Complete when you see confirmation the item was added to cart with the correct quantity."""

# Checkout prompt template
CHECKOUT_PROMPT_TEMPLATE = """Go to https://www.realcanadiansuperstore.ca/ and proceed through the checkout process.
The cart option should be at top right of the main page.

Navigate through all checkout steps:
- Delivery details: Click "select a time" and pick the next available time slot.
- Item details
- Contact details
- Driver tip
- Payment

Each step will need interaction and need to hit "Save & Continue" after each one.
Stop when you reach the final order review page where the "Place Order" button is visible and a dark green color."""

# Cart verification prompt template
VERIFY_CART_PROMPT_TEMPLATE = """Navigate to the shopping cart at Real Canadian Superstore and verify its contents.

Expected items: {expected_items}

Steps:
1. Go to https://www.realcanadiansuperstore.ca/en
2. Click on the cart icon at the top right
3. Read all items currently in the cart
4. Report which expected items are present and which are missing

Extract the exact names of all items in the cart."""


# =============================================================================
# Prompt Formatters
# =============================================================================


def get_login_prompt(
    username: str,
    password: str,
    template: str | None = None,
) -> str:
    """
    Format the login prompt with credentials.

    Args:
        username: Superstore account username
        password: Superstore account password
        template: Optional custom template (uses default if None)

    Returns:
        Formatted login prompt string
    """
    if template is None:
        template = LOGIN_PROMPT_TEMPLATE
    return template.format(username=username, password=password)


def get_add_item_prompt(
    item: str,
    template: str | None = None,
    check_login: bool = False,
) -> str:
    """
    Format the add item to cart prompt.

    Args:
        item: Grocery item to add (may include quantity)
        template: Optional custom template (uses default if None)
        check_login: If True, adds a login check note to the prompt

    Returns:
        Formatted add item prompt string
    """
    if template is None:
        template = ADD_ITEM_PROMPT_TEMPLATE

    prompt = template.format(item=item)

    if check_login:
        # Insert login check after first line
        lines = prompt.split("\n", 1)
        login_check = """
IMPORTANT: Before starting, check the page contains "My Shop" and "let's get started by shopping your regulars".
If you see these, you are already logged in.
NOTE: If you see a login page or are not logged in, report this as an error."""
        prompt = lines[0] + login_check + "\n" + lines[1] if len(lines) > 1 else lines[0] + login_check

    return prompt


def get_checkout_prompt(template: str | None = None) -> str:
    """
    Get the checkout prompt.

    Args:
        template: Optional custom template (uses default if None)

    Returns:
        Checkout prompt string
    """
    return template if template is not None else CHECKOUT_PROMPT_TEMPLATE


def get_verify_cart_prompt(
    expected_items: list[str],
    template: str | None = None,
) -> str:
    """
    Format the cart verification prompt.

    Args:
        expected_items: List of items expected to be in cart
        template: Optional custom template (uses default if None)

    Returns:
        Formatted verification prompt string
    """
    if template is None:
        template = VERIFY_CART_PROMPT_TEMPLATE
    return template.format(expected_items=", ".join(expected_items))


# =============================================================================
# Hydra Configuration Support
# =============================================================================


class PromptConfig:
    """
    Prompt configuration loaded from Hydra config.

    Usage:
        from core.prompts import PromptConfig

        # With Hydra config
        prompts = PromptConfig.from_hydra(cfg)

        # Get formatted prompts
        login_prompt = prompts.login(username="user", password="pass")
        add_item_prompt = prompts.add_item(item="milk")
    """

    def __init__(
        self,
        login_template: str | None = None,
        add_item_template: str | None = None,
        checkout_template: str | None = None,
        verify_cart_template: str | None = None,
    ):
        self.login_template = login_template
        self.add_item_template = add_item_template
        self.checkout_template = checkout_template
        self.verify_cart_template = verify_cart_template

    @classmethod
    def from_hydra(cls, cfg: DictConfig) -> "PromptConfig":
        """
        Create PromptConfig from Hydra configuration.

        Expected config structure:
            prompts:
              login: "Custom login prompt with {username} and {password}"
              add_item: "Custom add item prompt with {item}"
              checkout: "Custom checkout prompt"
              verify_cart: "Custom verify prompt with {expected_items}"
        """
        prompts_cfg = cfg.get("prompts", {})

        return cls(
            login_template=prompts_cfg.get("login"),
            add_item_template=prompts_cfg.get("add_item"),
            checkout_template=prompts_cfg.get("checkout"),
            verify_cart_template=prompts_cfg.get("verify_cart"),
        )

    def login(self, username: str, password: str) -> str:
        """Get formatted login prompt."""
        return get_login_prompt(username, password, self.login_template)

    def add_item(self, item: str, check_login: bool = False) -> str:
        """Get formatted add item prompt."""
        return get_add_item_prompt(item, self.add_item_template, check_login)

    def checkout(self) -> str:
        """Get checkout prompt."""
        return get_checkout_prompt(self.checkout_template)

    def verify_cart(self, expected_items: list[str]) -> str:
        """Get formatted cart verification prompt."""
        return get_verify_cart_prompt(expected_items, self.verify_cart_template)


# Default instance for simple usage without Hydra
default_prompts = PromptConfig()
