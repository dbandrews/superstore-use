# superstore-use

Grocery shopping agent for Real Canadian Superstore. Uses browser automation with Groq LLMs for intelligent shopping.

## Quick Start (Local)

### 1. Install Dependencies

```bash
uv sync
uvx playwright install chromium --with-deps --no-shell
```

### 2. Configure Environment

Create `.env` file:

```bash
GROQ_API_KEY=your_groq_api_key_here
SUPERSTORE_USER=your_email@example.com
SUPERSTORE_PASSWORD=your_password
```

Get a Groq API key, add some money to your account at [https://console.groq.com](https://console.groq.com)

### 3. Login (First Time)

```bash
uv run -m src.local.cli login
```

This saves your session to `superstore-profile/` for reuse so shopping will add to your profile's cart.

### 4. Run Local Shopping

```bash
uv run -m src.local.cli shop
```

Opens parallel browser windows to add items to your cart.

## Deploy to Modal

### 1. Authenticate Modal

```bash
uv run modal setup
```

### 2. Configure App Naming

**Change app name** in `config.toml`:
```toml
[app]
name = "my-unique-app-name-12345" 
```

### 3. Create Modal Secrets

Go to [https://modal.com/secrets](https://modal.com/secrets) and create:

**groq-secret:**
- `GROQ_API_KEY`: Your Groq API key

**superstore:**
- `SUPERSTORE_USER`: Your Superstore email
- `SUPERSTORE_PASSWORD`: Your Superstore password

**web-auth:**
- `WEB_AUTH_TOKEN`: Random secret token (e.g., run `openssl rand -hex 32` in your terminal) that will be required to access the web UI.

**oxy-proxy** (optional, for proxy support):
- `PROXY_SERVER`: Proxy server URL
- `PROXY_USERNAME`: Proxy username
- `PROXY_PASSWORD`: Proxy password

### 4. Deploy

```bash
uv run modal deploy modal/app.py
```

Your web UI will be available at the URL shown in the output.

**Access your web UI** with the auth token:
```
https://your-workspace--your-app-name-web.modal.run?token=YOUR_WEB_AUTH_TOKEN
```

### 5. View Logs

```bash
uv run modal app logs superstore-agent
```

## Architecture

```
superstore-use/
  modal/              # Modal deployment
    app.py            # Main Modal app
    templates/        # Web UI HTML
    static/           # CSS & JavaScript
  src/
    core/             # Shared utilities
    local/            # Local CLI
    prompts/          # AI prompts
  config.toml         # Configuration
```

**Local:** Runs with visible browser windows using `src.local.cli`
**Modal:** Serverless cloud deployment with web UI at `modal/app.py`

## Security

**Important**: Your deployed web endpoint is accessible via a predictable URL. To prevent unauthorized access, you must set the WEB_AUTH_TOKEN secret in Modal.

1. **Set WEB_AUTH_TOKEN** secret in Modal, keep this handy for accessing the web UI.
2. **Never commit** your auth token or share your endpoint URL publicly

Without these steps, anyone could potentially access your shopping cart.

## Commands

```bash
# Local
uv run -m src.local.cli login          # Save login session
uv run -m src.local.cli login --headed # Login with visible browser
uv run -m src.local.cli shop           # Interactive shopping

# Modal
uv run modal deploy modal/app.py     # Deploy to cloud
uv run modal serve modal/app.py      # Local development with hot-reload
uv run modal app list                # List deployed apps
uv run modal app logs superstore-agent  # Stream logs
```
