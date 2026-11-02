import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("BrowserConfig")


def get_effective_key(user_key: Optional[str] = None) -> str:
    """
    Returns effective key from user_key, falling back to standard NTTM secure salt key.
    """
    from .crypto import get_effective_key as _get_eff_key

    return _get_eff_key(user_key)


@dataclass
class BrowserConfig:
    """
    Stores and manages configuration for GitHub Offline Browser.
    Persisted at ~/.gh_offline_browser/config.json.
    """

    github_token: str = ""
    github_repo: str = ""  # Format: "owner/repo"
    branch: str = "main"
    default_search_url: str = "https://www.google.com/search?q={query}"
    chunk_size_mb: int = 50
    poll_interval_seconds: float = 1.0
    timeout_seconds: int = 240
    downloads_dir: str = ""
    encryption_key: str = ""
    onboarding_completed: bool = False

    def __post_init__(self):
        if not self.downloads_dir:
            home = Path.home()
            downloads = home / "Downloads" / "GitHubBrowser"
            downloads.mkdir(parents=True, exist_ok=True)
            self.downloads_dir = str(downloads)

    def get_effective_key(self) -> str:
        """
        Returns the effective AES-256-GCM encryption key.
        If user has not configured a custom key, returns the standard secure project key.
        """
        return get_effective_key(self.encryption_key)

    @classmethod
    def get_config_path(cls) -> Path:
        home_dir = Path.home()
        config_dir = home_dir / ".gh_offline_browser"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"

    @classmethod
    def load(cls) -> "BrowserConfig":
        config_path = cls.get_config_path()
        if not config_path.exists():
            default_cfg = cls()
            default_cfg.save()
            return default_cfg

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                github_token=data.get("github_token", ""),
                github_repo=data.get("github_repo", ""),
                branch=data.get("branch", "main"),
                default_search_url=data.get(
                    "default_search_url", "https://www.google.com/search?q={query}"
                ),
                chunk_size_mb=data.get("chunk_size_mb", 50),
                poll_interval_seconds=1.0 if data.get("poll_interval_seconds") in (2.0, 3.0, None) else float(data.get("poll_interval_seconds", 1.0)),
                timeout_seconds=data.get("timeout_seconds", 240),
                downloads_dir=data.get("downloads_dir", ""),
                encryption_key=data.get("encryption_key", ""),
                onboarding_completed=bool(data.get("onboarding_completed", False)),
            )
        except Exception as e:
            logger.warning(f"Failed to read config file: {e}. Returning default config.")
            return cls()

    def save(self) -> None:
        config_path = self.get_config_path()
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
            logger.info(f"Saved configuration to {config_path}")
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")

    def is_configured(self) -> bool:
        """Returns True if minimum required credentials (token + repo) are present."""
        return bool(
            self.github_token
            and self.github_token.strip()
            and self.github_repo
            and "/" in self.github_repo
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
