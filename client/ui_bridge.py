from __future__ import annotations

"""
Webview API Bridge for N0PPERABO offline browser.
Provides JavaScript-to-Python bridge methods supporting Multi-Tab, Action Replay,
Batch Mode, Shorts Navigation, Configuration, and Daemon control.
"""

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .browser_ui import OfflineBrowserUI

logger = logging.getLogger("BrowserUI")


class WebviewApiBridge:
    """Python bridge exposed to JavaScript under window.pywebview.api."""

    def __init__(self, ui: OfflineBrowserUI):
        # Must use leading underscore so pywebview does not inspect ui.window.native recursively
        self._ui = ui

    def get_current_url(self) -> Dict[str, str]:
        return {"url": self._ui.current_url or ""}

    def set_delay(self, seconds: Any = 0):
        self._ui.set_delay(seconds)

    def set_custom_delay(self, seconds: Any = 0):
        self._ui.set_delay(seconds)

    def get_delay(self) -> int:
        return self._ui.current_delay_seconds

    def set_scroll(self, steps: Any = 0):
        self._ui.set_scroll(steps)

    def set_custom_scroll(self, steps: Any = 0):
        self._ui.set_scroll(steps)

    def get_scroll(self) -> int:
        return self._ui.current_scroll_steps

    def navigate(
        self, target_url: str, custom_delay_seconds: int = 0, tab_id: str = "", custom_scroll_steps: int = 0
    ):
        self._ui.dispatch_navigate(
            target_url, custom_delay_seconds, tab_id=tab_id, custom_scroll_steps=custom_scroll_steps
        )

    def navigate_shorts_next(self, current_video_id: str = "", tab_id: str = "") -> Dict[str, Any]:
        actions = [{"type": "shorts_next", "wait_after_ms": 2500}]
        self._ui.dispatch_replay(self._ui.current_url, actions, tab_id=tab_id)
        return {"status": "ok"}

    def submit_form(
        self,
        action_url: str,
        method: str,
        content_type: str,
        body_base64: str,
        custom_delay_seconds: int = 0,
        tab_id: str = "",
        custom_scroll_steps: int = 0,
    ):
        self._ui.dispatch_form_submit(
            action_url,
            method,
            content_type,
            body_base64,
            custom_delay_seconds,
            tab_id=tab_id,
            custom_scroll_steps=custom_scroll_steps,
        )

    def dispatch_replay(
        self,
        target_url: str,
        actions: List[Dict[str, Any]],
        intercepted_ajax: Optional[Dict[str, Any]] = None,
        custom_delay_seconds: int = 0,
        tab_id: str = "",
        custom_scroll_steps: int = 0,
    ):
        self._ui.dispatch_replay(
            target_url,
            actions,
            intercepted_ajax,
            custom_delay_seconds,
            tab_id=tab_id,
            custom_scroll_steps=custom_scroll_steps,
        )

    def run_batch_actions(
        self,
        target_url: str,
        actions: List[Dict[str, Any]],
        custom_delay_seconds: int = 0,
        tab_id: str = "",
        custom_scroll_steps: int = 0,
    ):
        self._ui.dispatch_replay(
            target_url,
            actions,
            None,
            custom_delay_seconds,
            tab_id=tab_id,
            custom_scroll_steps=custom_scroll_steps,
        )

    def set_batch_mode(self, enabled: bool):
        self._ui.is_batch_mode = bool(enabled)

    def clear_batch_queue(self):
        self._ui.action_queue.clear()

    def send_batch(self, actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        items = actions if actions is not None else list(self._ui.action_queue)
        if items:
            self._ui.action_queue.clear()
            self._ui.dispatch_replay(self._ui.current_url, items)
        return {"status": "ok"}

    def open_downloads_folder(self):
        self._ui.open_downloads_folder()

    def clear_session(self):
        self._ui.clear_session()

    def go_back(self):
        self._ui.go_back()

    def go_forward(self):
        self._ui.go_forward()

    def reload(self):
        self._ui.reload()

    def go_home(self):
        self._ui.go_home()

    def get_config(self) -> Dict[str, Any]:
        return {
            "token": getattr(self._ui.config, "github_token", ""),
            "repo": getattr(self._ui.config, "github_repo", ""),
            "branch": getattr(self._ui.config, "branch", "main"),
            "encryption_key": getattr(self._ui.config, "encryption_key", ""),
        }

    def get_daemon_status(self) -> Dict[str, Any]:
        alive = self._ui.transport.is_daemon_alive()
        return {"alive": alive}

    def toggle_daemon(self, enable: Optional[bool] = None) -> Dict[str, Any]:
        alive = self._ui.transport.is_daemon_alive()
        target = not alive if enable is None else bool(enable)
        if not target and alive:
            ok, msg = self._ui.transport.stop_daemon()
            return {"alive": False, "msg": "رانر زنده متوقف شد."}
        elif target and not alive:
            ok, msg = self._ui.transport.start_daemon(idle_timeout_min=30)
            return {"alive": True, "msg": "درخواست راه‌اندازی رانر زنده ارسال شد. ظرف ۳۰ الی ۴۵ ثانیه آماده می‌شود..."}
        return {"alive": alive, "msg": ""}

    def save_config(
        self, token: str, repo: str, branch: str = "main", search_url: str = "", encryption_key: str = ""
    ) -> Dict[str, Any]:
        try:
            self._ui.config.github_token = token.strip()
            self._ui.config.github_repo = repo.strip()
            if branch:
                self._ui.config.branch = branch.strip()
            if search_url:
                self._ui.config.default_search_url = search_url.strip()
            self._ui.config.encryption_key = encryption_key.strip()
            self._ui.config.save()
            return {"success": True}
        except Exception as e:
            logger.error(f"Save config error: {e}")
            return {"success": False, "error": str(e)}

    def complete_onboarding(self) -> Dict[str, Any]:
        """Marks the first-time onboarding walkthrough as completed."""
        try:
            self._ui.config.onboarding_completed = True
            self._ui.config.save()
            return {"success": True}
        except Exception as e:
            logger.error(f"Complete onboarding error: {e}")
            return {"success": False, "error": str(e)}

    def get_onboarding_status(self) -> Dict[str, Any]:
        return {"completed": bool(getattr(self._ui.config, "onboarding_completed", False))}

    # ---------------- SQLite Storage DB (History & Bookmarks) ----------------

    def get_history(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        return self._ui.storage_db.get_history(limit=int(limit or 50), offset=int(offset or 0))

    def search_history(self, query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        return self._ui.storage_db.search_history(query=query or "", limit=int(limit or 20))

    def clear_history(self) -> Dict[str, Any]:
        success = self._ui.storage_db.clear_history()
        return {"success": success}

    def add_bookmark(self, url: str = "", title: str = "", folder: str = "default") -> Dict[str, Any]:
        clean_url = url or self._ui.current_url
        success = self._ui.storage_db.add_bookmark(clean_url, title=title, folder=folder)
        return {"success": success, "url": clean_url}

    def get_bookmarks(self, folder: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._ui.storage_db.get_bookmarks(folder=folder)

    def remove_bookmark(self, bookmark_id_or_url: Any = "") -> Dict[str, Any]:
        success = self._ui.storage_db.remove_bookmark(bookmark_id_or_url)
        return {"success": success}

    def is_bookmarked(self, url: str = "") -> Dict[str, Any]:
        target = url or self._ui.current_url
        return {"bookmarked": self._ui.storage_db.is_bookmarked(target)}

    def get_storage_stats(self) -> Dict[str, Any]:
        return self._ui.storage_db.get_stats()
