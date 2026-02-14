"""Configuration management for Superstore Agent.

Provides centralized configuration loading from config.toml with type-safe
access via Pydantic models. Supports environment-specific settings for local
development and Modal deployment.
"""

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Pydantic Configuration Models
# =============================================================================


class AppConfig(BaseModel):
    """Application-level configuration."""

    name: str = "superstore-agent"
    base_url: str = "https://www.realcanadiansuperstore.ca/en"


class BrowserTimingConfig(BaseModel):
    """Browser timing settings for a specific environment/task."""

    wait_between_actions: float = 4.0
    min_wait_page_load: float = 2.5
    wait_for_network_idle: float = 2.5


class BrowserLocalConfig(BaseModel):
    """Local development browser settings."""

    headless: bool = False
    profile_dir: str = "./superstore-profile"
    window_width: int = 700
    window_height: int = 700
    use_proxy: bool = False
    use_stealth: bool = False
    timing: BrowserTimingConfig = Field(default_factory=BrowserTimingConfig)


class BrowserModalLoginConfig(BaseModel):
    """Modal login-specific timing settings."""

    wait_between_actions: float = 5.0
    min_wait_page_load: float = 5.0
    wait_for_network_idle: float = 5.0


class BrowserModalAddItemConfig(BaseModel):
    """Modal add_item-specific timing settings."""

    wait_between_actions: float = 1.0
    min_wait_page_load: float = 1.0
    wait_for_network_idle: float = 2.5


class BrowserModalLoginCheckConfig(BaseModel):
    """Modal login pre-check timing - fast DOM-only check (no LLM)."""

    wait_between_actions: float = 0.5
    min_wait_page_load: float = 0.5
    wait_for_network_idle: float = 0.5


class BrowserModalViewCartConfig(BaseModel):
    """Modal view_cart timing - slower to let JS-heavy cart page render."""

    wait_between_actions: float = 2.0
    min_wait_page_load: float = 3.0
    wait_for_network_idle: float = 3.0


class BrowserModalConfig(BaseModel):
    """Modal deployment browser settings."""

    headless: bool = True
    profile_dir: str = "/session/profile"
    fallback_profile_dir: str = "/app/superstore-profile"
    window_width: int = 1920
    window_height: int = 1080
    use_proxy: bool = True
    use_stealth: bool = True
    login_check: BrowserModalLoginCheckConfig = Field(default_factory=BrowserModalLoginCheckConfig)
    login: BrowserModalLoginConfig = Field(default_factory=BrowserModalLoginConfig)
    add_item: BrowserModalAddItemConfig = Field(default_factory=BrowserModalAddItemConfig)
    view_cart: BrowserModalViewCartConfig = Field(default_factory=BrowserModalViewCartConfig)


class BrowserConfig(BaseModel):
    """Browser configuration."""

    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    stealth_args: list[str] = Field(
        default_factory=lambda: [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-accelerated-2d-canvas",
            "--disable-gpu",
        ]
    )
    timeout_browser_start: int = 120
    timeout_browser_launch: int = 120
    timeout_browser_state_request: int = 120
    # Navigation timeouts - default 15s/10s in browser-use is too short for slow sites
    timeout_navigate_to_url: int = 60
    timeout_switch_tab: int = 30
    local: BrowserLocalConfig = Field(default_factory=BrowserLocalConfig)
    modal: BrowserModalConfig = Field(default_factory=BrowserModalConfig)


class LLMConfig(BaseModel):
    """LLM configuration."""

    chat_model: str = "gpt-4.1"
    chat_temperature: float = 0.0
    browser_model: str = "openai/gpt-oss-120b"
    browser_use_vision: bool = False


class AgentConfig(BaseModel):
    """Agent step limits."""

    max_steps_login: int = 50
    max_steps_add_item: int = 30
    max_steps_checkout: int = 100
    max_steps_place_order: int = 10
    max_steps_view_cart: int = 20


class PromptsConfig(BaseModel):
    """Paths to prompt template files (markdown format)."""

    chat_system: str = "prompts/chat_system.md"
    login: str = "prompts/login.md"
    add_item: str = "prompts/add_item.md"
    checkout: str = "prompts/checkout.md"
    view_cart: str = "prompts/view_cart.md"


class SuccessDetectionConfig(BaseModel):
    """Success detection configuration."""

    indicators: list[str] = Field(
        default_factory=lambda: [
            "added to cart",
            "add to cart",
            "item added",
            "cart updated",
            "in your cart",
            "added to your cart",
            "quantity updated",
        ]
    )


class LocalCLIConfig(BaseModel):
    """Local CLI configuration."""

    default_monitor_offset: int = 1080
    max_parallel_workers: int = 4
    window_gap: int = 20
    window_y_offset: int = 50
    chrome_lock_files: list[str] = Field(
        default_factory=lambda: [
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
            "lockfile",
            "parent.lock",
        ]
    )


class Config(BaseModel):
    """Root configuration model."""

    app: AppConfig = Field(default_factory=AppConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    success_detection: SuccessDetectionConfig = Field(default_factory=SuccessDetectionConfig)
    local_cli: LocalCLIConfig = Field(default_factory=LocalCLIConfig)

    def load_prompt(self, name: str, **kwargs) -> str:
        """Load and format a prompt template.

        Args:
            name: Prompt name (e.g., "login", "add_item", "chat_system")
            **kwargs: Template variables for string formatting

        Returns:
            Formatted prompt string
        """
        prompt_path = getattr(self.prompts, name, None)
        if not prompt_path:
            raise ValueError(f"Unknown prompt: {name}")

        # Try multiple base paths for prompt files
        base_paths = [
            Path.cwd(),  # Current working directory
            Path(__file__).parent.parent,  # Project root (relative to core/)
            Path("/app"),  # Modal container path
        ]

        for base in base_paths:
            full_path = base / prompt_path
            if full_path.exists():
                content = full_path.read_text()
                if kwargs:
                    content = content.format(**kwargs)
                return content

        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")


# =============================================================================
# Configuration Loading
# =============================================================================


def _find_config_file() -> Optional[Path]:
    """Find config.toml in standard locations."""
    search_paths = [
        Path.cwd() / "config.toml",  # Current working directory
        Path(__file__).parent.parent / "config.toml",  # Project root
        Path("/app/config.toml"),  # Modal container
    ]

    for path in search_paths:
        if path.exists():
            return path

    return None


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load configuration from config.toml.

    Uses lru_cache to ensure config is only loaded once per process.

    Returns:
        Config instance with all settings
    """
    config_path = _find_config_file()

    if config_path:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return Config.model_validate(data)

    # Return defaults if no config file found
    return Config()


def is_modal_environment() -> bool:
    """Check if running in Modal container environment."""
    return os.path.exists("/session") or os.environ.get("IN_DOCKER") == "True"


def get_stealth_args(config: Optional[Config] = None) -> list[str]:
    """Get browser stealth arguments with user agent.

    Args:
        config: Config instance (uses load_config() if None)

    Returns:
        List of browser arguments for stealth mode
    """
    if config is None:
        config = load_config()

    args = list(config.browser.stealth_args)
    args.append(f"--user-agent={config.browser.user_agent}")
    return args


# =============================================================================
# Convenience Functions
# =============================================================================


def get_config() -> Config:
    """Get the loaded configuration (alias for load_config)."""
    return load_config()
