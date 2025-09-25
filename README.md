# superstore-use

an agent for grocery shopping


# Setup

```bash
uv sync
```

```bash
uvx playwright install chromium --with-deps --no-shell
```

Get OpenAI api key - add to .env as `OPENAI_API_KEY`. Add `SUPERSTORE_USER`, `SUPERSTORE_PASSWORD` to .env as well for login.


# Run

```bash
uv run main.py
```

