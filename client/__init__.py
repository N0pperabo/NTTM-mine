"""
GitHub-Based Offline Browser Client Module
"""

from .config import BrowserConfig
from .session_manager import SessionManager
from .transport import GitHubTransport, FetchResult

__all__ = ["BrowserConfig", "SessionManager", "GitHubTransport", "FetchResult"]
