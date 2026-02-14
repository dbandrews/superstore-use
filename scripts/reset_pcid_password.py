"""Reset PCid password via browser-use agent.

Uses the system Chrome with the user's existing profile (already logged into
Gmail) to find a PCid password reset email, click through, and change the
password.

Usage:
    # Close Chrome first, then:
    uv run python scripts/reset_pcid_password.py
"""

import asyncio
import os

from browser_use import Agent, Browser, ChatAnthropic
from dotenv import load_dotenv

load_dotenv()

TASK = """\
1. Navigate to https://mail.google.com/mail/?tab=wm#inbox
2. Wait for the inbox to fully load.
3. Find and click the email with a subject containing "Reset your PCid password now".
4. Inside the email, click the reset password button or link.
5. A new tab will open — wait for it to fully load (it can be very slow).
6. On the password reset page, enter x_new_password in both password fields.
7. Submit the form.
"""


async def main():
    new_password = os.environ.get("SUPERSTORE_PASSWORD")
    if not new_password:
        raise RuntimeError("SUPERSTORE_PASSWORD environment variable is not set")

    browser = Browser(
        executable_path="/usr/bin/google-chrome",
        user_data_dir="/home/drumm/.config/google-chrome",
        profile_directory="Default",
        headless=False,
    )

    llm = ChatAnthropic(model="claude-sonnet-4-5-20250929")

    agent = Agent(
        task=TASK,
        llm=llm,
        browser=browser,
        sensitive_data={"x_new_password": new_password},
    )

    await agent.run(max_steps=50)


if __name__ == "__main__":
    asyncio.run(main())
