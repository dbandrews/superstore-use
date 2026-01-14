"""
Configurable LLM Factory for browser-use and chat agents.

Supports multiple providers:
- OpenAI (ChatOpenAI from browser-use)
- Anthropic (ChatAnthropic from browser-use)
- OpenRouter (via ChatOpenAI with custom base_url)

Usage:
    from core.llm import create_llm, create_llm_from_config

    # Using Hydra config
    llm = create_llm_from_config(cfg.model)

    # Direct creation
    llm = create_llm(provider="openai", name="gpt-4.1")
"""

import os
from typing import Any

from omegaconf import DictConfig


def create_llm(
    provider: str,
    name: str,
    temperature: float = 0,
    api_key: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
):
    """
    Create an LLM instance based on provider.

    Args:
        provider: LLM provider - "openai", "anthropic", or "openrouter"
        name: Model name (e.g., "gpt-4.1", "claude-sonnet-4-0")
        temperature: Sampling temperature (default: 0)
        api_key: Direct API key (optional, prefer api_key_env)
        api_key_env: Environment variable name for API key
        base_url: Custom base URL (required for OpenRouter)
        **kwargs: Additional provider-specific arguments

    Returns:
        LLM instance compatible with browser-use Agent

    Raises:
        ValueError: If provider is unknown or API key is missing
    """
    # Resolve API key from environment if not provided directly
    if api_key is None and api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(
                f"API key not found. Set the {api_key_env} environment variable."
            )

    provider = provider.lower()

    if provider == "openai":
        from browser_use import ChatOpenAI

        llm_kwargs = {
            "model": name,
            "temperature": temperature,
        }
        if api_key:
            llm_kwargs["api_key"] = api_key
        if base_url:
            llm_kwargs["base_url"] = base_url

        return ChatOpenAI(**llm_kwargs, **kwargs)

    elif provider == "anthropic":
        from browser_use import ChatAnthropic

        llm_kwargs = {
            "model": name,
            "temperature": temperature,
        }
        if api_key:
            llm_kwargs["api_key"] = api_key

        return ChatAnthropic(**llm_kwargs, **kwargs)

    elif provider == "openrouter":
        # OpenRouter uses OpenAI-compatible API with custom base_url
        from browser_use import ChatOpenAI

        if not base_url:
            base_url = "https://openrouter.ai/api/v1"

        llm_kwargs = {
            "model": name,
            "temperature": temperature,
            "base_url": base_url,
        }
        if api_key:
            llm_kwargs["api_key"] = api_key

        # OpenRouter-specific headers can be passed via default_headers
        headers = {}
        if kwargs.get("site_url"):
            headers["HTTP-Referer"] = kwargs.pop("site_url")
        if kwargs.get("site_name"):
            headers["X-Title"] = kwargs.pop("site_name")
        if headers:
            llm_kwargs["default_headers"] = headers

        return ChatOpenAI(**llm_kwargs, **kwargs)

    else:
        raise ValueError(
            f"Unknown provider: {provider}. "
            f"Supported providers: openai, anthropic, openrouter"
        )


def create_llm_from_config(model_cfg: DictConfig):
    """
    Create an LLM instance from a Hydra model configuration.

    Args:
        model_cfg: Hydra DictConfig with model settings
            Required: provider, name
            Optional: temperature, api_key_env, base_url, etc.

    Returns:
        LLM instance compatible with browser-use Agent

    Example config (conf/model/openai.yaml):
        provider: openai
        name: gpt-4.1
        temperature: 0
        api_key_env: OPENAI_API_KEY
    """
    # Convert DictConfig to dict for easier handling
    cfg_dict = dict(model_cfg)

    # Extract required fields
    provider = cfg_dict.pop("provider")
    name = cfg_dict.pop("name")

    # Pass remaining config as kwargs
    return create_llm(provider=provider, name=name, **cfg_dict)


def get_model_info(model_cfg: DictConfig) -> dict:
    """
    Get human-readable info about a model configuration.

    Args:
        model_cfg: Hydra DictConfig with model settings

    Returns:
        Dict with provider, name, and display_name
    """
    provider = model_cfg.provider
    name = model_cfg.name

    # Create display name
    display_name = f"{provider}/{name}"

    return {
        "provider": provider,
        "name": name,
        "display_name": display_name,
        "temperature": model_cfg.get("temperature", 0),
    }
