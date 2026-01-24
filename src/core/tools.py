"""Custom browser-use tools for human-like interactions.

Provides tools that can be registered with browser-use Agent via the Tools class.
These tools add more realistic, human-like behavior to browser automation.
"""

import asyncio
import random

from browser_use import ActionResult
from browser_use.browser.session import BrowserSession


def create_typing_tools(
    min_delay_ms: int = 50,
    max_delay_ms: int = 150,
    typo_probability: float = 0.0,
    pause_probability: float = 0.1,
    pause_min_ms: int = 200,
    pause_max_ms: int = 500,
):
    """Create a Tools instance with human-like typing actions.

    Args:
        min_delay_ms: Minimum delay between keystrokes in milliseconds.
        max_delay_ms: Maximum delay between keystrokes in milliseconds.
        typo_probability: Probability of making a typo (0.0 to 1.0). Currently unused.
        pause_probability: Probability of a longer pause between characters.
        pause_min_ms: Minimum pause duration in milliseconds.
        pause_max_ms: Maximum pause duration in milliseconds.

    Returns:
        Tools instance with typing actions registered.
    """
    from browser_use import Tools

    tools = Tools()

    @tools.action(
        description=(
            "Type text with human-like delays between characters. "
            "Use this for entering sensitive information like email addresses and passwords "
            "to avoid bot detection. The text is typed character by character with random delays."
        )
    )
    async def human_type(
        text: str,
        browser_session: BrowserSession,
        press_enter_after: bool = False,
    ) -> ActionResult:
        """Type text with human-like random delays between keystrokes.

        Args:
            text: The text to type.
            browser_session: Browser session (automatically injected).
            press_enter_after: Whether to press Enter after typing.

        Returns:
            ActionResult indicating success or failure.
        """
        try:
            page = await browser_session.get_current_page()
            if not page:
                return ActionResult(error="No active page found")

            # Type each character with random delay
            for i, char in enumerate(text):
                # Calculate delay for this character
                delay_ms = random.randint(min_delay_ms, max_delay_ms)

                # Occasionally add a longer pause (simulates human reading/thinking)
                if random.random() < pause_probability:
                    delay_ms += random.randint(pause_min_ms, pause_max_ms)

                # Type the character
                await page.keyboard.type(char, delay=0)

                # Wait before next character
                await asyncio.sleep(delay_ms / 1000.0)

            if press_enter_after:
                await asyncio.sleep(random.randint(min_delay_ms, max_delay_ms) / 1000.0)
                await page.keyboard.press("Enter")

            return ActionResult(
                extracted_content=f"Typed {len(text)} characters with human-like delays"
            )

        except Exception as e:
            return ActionResult(error=f"Failed to type text: {str(e)}")

    @tools.action(
        description=(
            "Enter email address into the currently focused input field with human-like typing. "
            "Use this specifically for email fields during login or registration."
        )
    )
    async def type_email(
        email: str,
        browser_session: BrowserSession,
    ) -> ActionResult:
        """Type an email address with human-like delays.

        Args:
            email: The email address to type.
            browser_session: Browser session (automatically injected).

        Returns:
            ActionResult indicating success or failure.
        """
        try:
            page = await browser_session.get_current_page()
            if not page:
                return ActionResult(error="No active page found")

            # Type each character with random delay
            for char in email:
                delay_ms = random.randint(min_delay_ms, max_delay_ms)

                # Slightly longer pause after @ and . in emails (natural hesitation)
                if char in ("@", "."):
                    delay_ms += random.randint(50, 150)

                await page.keyboard.type(char, delay=0)
                await asyncio.sleep(delay_ms / 1000.0)

            return ActionResult(
                extracted_content=f"Typed email address ({len(email)} characters)"
            )

        except Exception as e:
            return ActionResult(error=f"Failed to type email: {str(e)}")

    @tools.action(
        description=(
            "Enter password into the currently focused password field with human-like typing. "
            "Use this specifically for password fields during login or registration. "
            "The password is typed securely character by character."
        )
    )
    async def type_password(
        password: str,
        browser_session: BrowserSession,
    ) -> ActionResult:
        """Type a password with human-like delays.

        Args:
            password: The password to type.
            browser_session: Browser session (automatically injected).

        Returns:
            ActionResult indicating success or failure.
        """
        try:
            page = await browser_session.get_current_page()
            if not page:
                return ActionResult(error="No active page found")

            # Type each character with random delay
            for char in password:
                delay_ms = random.randint(min_delay_ms, max_delay_ms)

                # Add occasional pauses (people sometimes hesitate during password entry)
                if random.random() < pause_probability:
                    delay_ms += random.randint(pause_min_ms, pause_max_ms)

                await page.keyboard.type(char, delay=0)
                await asyncio.sleep(delay_ms / 1000.0)

            return ActionResult(
                extracted_content=f"Typed password ({len(password)} characters)"
            )

        except Exception as e:
            return ActionResult(error=f"Failed to type password: {str(e)}")

    return tools


def get_default_typing_tools():
    """Get typing tools with default configuration.

    Returns:
        Tools instance with default typing configuration.
    """
    from src.core.config import load_config

    config = load_config()

    return create_typing_tools(
        min_delay_ms=config.typing.min_delay_ms,
        max_delay_ms=config.typing.max_delay_ms,
        typo_probability=config.typing.typo_probability,
        pause_probability=config.typing.pause_probability,
        pause_min_ms=config.typing.pause_min_ms,
        pause_max_ms=config.typing.pause_max_ms,
    )
