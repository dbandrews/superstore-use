"""API-based tools for Superstore Agent.

This module provides direct API access to Real Canadian Superstore,
replacing browser automation for product search and cart operations.

Main components:
- credentials: Credential extraction from browser sessions
- client: SuperstoreAPIClient for API calls
- tools: LangGraph tools for agent use
- agent: API-based chat agent

Usage:
    # Run the interactive CLI
    uv run -m src.api.cli

    # Quick API test
    uv run -m src.api.cli --test
"""

from src.api.credentials import SuperstoreCredentials, extract_credentials_from_page
from src.api.client import SuperstoreAPIClient, ProductSearchResult, CartEntry
from src.api.tools import API_TOOLS, set_credentials, get_client, initialize_anonymous_session
from src.api.agent import create_api_agent
from src.api.modal_agent import create_api_modal_agent

__all__ = [
    # Credentials
    "SuperstoreCredentials",
    "extract_credentials_from_page",
    # Client
    "SuperstoreAPIClient",
    "ProductSearchResult",
    "CartEntry",
    # Tools
    "API_TOOLS",
    "set_credentials",
    "get_client",
    "initialize_anonymous_session",
    # Agent
    "create_api_agent",
    "create_api_modal_agent",
]
