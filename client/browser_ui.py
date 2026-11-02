import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import webview

from .config import BrowserConfig
from .session_manager import SessionManager
from .storage_db import StorageDB, get_storage_db
from .transport import FetchResult, GitHubTransport
from .ui_server import LocalAssetHandler
from .ui_bridge import WebviewApiBridge
from .ui_templates import generate_shell_html, generate_start_page_html

__all__ = [
    "OfflineBrowserUI",
    "LocalAssetHandler",
    "WebviewApiBridge",
    "generate_shell_html",
    "generate_start_page_html",
]

logger = logging.getLogger("BrowserUI")


class OfflineBrowserUI:
    """
    PyWebView Shell, Action Queue UI, AJAX Trap & Deep DOM Interceptor.
    """

    def __init__(self, transport: GitHubTransport, session_manager: SessionManager):
        self.transport = transport
        self.config = transport.config
        self.session_manager = session_manager
        self.storage_db: StorageDB = get_storage_db()
        self.window = None
        self.server_port = 0
        self.server_thread = None
        self._browser_mode: bool = False
        self._js_events: List[str] = []
        self._js_events_lock = threading.Lock()

        # History navigation
        self.history: List[Dict[str, str]] = []
        self.history_index: int = -1

        # Current navigation state
        self.current_url: str = "about:blank"
        self.is_busy: bool = False
        self.is_batch_mode: bool = False
        self.action_queue: List[Dict[str, Any]] = []
        self.current_delay_seconds: int = 0
        self.current_scroll_steps: int = 0

    def push_js_event(self, js_code: str):
        with self._js_events_lock:
            self._js_events.append(js_code)

    def pop_js_events(self) -> List[str]:
        with self._js_events_lock:
            events = list(self._js_events)
            self._js_events.clear()
            return events

    def start_local_server(self) -> int:
        """Starts the local HTTP server on a free port."""
        LocalAssetHandler.cache_dir = self.transport.cache_base
        LocalAssetHandler.shell_html = self._generate_shell_html()
        LocalAssetHandler.start_html = self._generate_start_page_html()
        LocalAssetHandler.ui = self
        LocalAssetHandler.api = WebviewApiBridge(self)

        server = HTTPServer(("127.0.0.1", 0), LocalAssetHandler)
        self.server_port = server.server_port

        def run_server():
            server.serve_forever()

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Local UI server running at http://127.0.0.1:{self.server_port}/")
        return self.server_port

    def launch(self, browser_mode: bool = False):
        """Launches the PyWebView window or opens the system default browser."""
        self._browser_mode = browser_mode
        port = self.start_local_server()
        url = f"http://127.0.0.1:{port}/"

        if browser_mode:
            import webbrowser
            logger.info(f"Opening default browser at {url} (--browser mode)...")
            webbrowser.open(url)
            logger.info("NTTM Server is active. Press Ctrl+C in terminal to stop.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Browser mode closed by user.")
            return

        api = WebviewApiBridge(self)

        self.window = webview.create_window(
            title="N0PPERABO - NTTM Offline Browser",
            url=url,
            js_api=api,
            width=1366,
            height=850,
            min_size=(1024, 700),
            background_color="#050608",
        )
        webview.start(debug=False)

    # ---------------- UI Action Handlers ----------------

    def set_delay(self, seconds: Any):
        try:
            self.current_delay_seconds = max(0, int(seconds or 0))
        except Exception:
            self.current_delay_seconds = 0
        logger.info(f"Custom delay set to {self.current_delay_seconds}s in Python UI state.")

    def get_and_consume_delay(self, param_delay: Any = 0) -> int:
        """Returns the delay in seconds and resets it back to 0."""
        delay = 0
        try:
            delay = int(param_delay or 0)
        except Exception:
            delay = 0
        if delay <= 0:
            delay = self.current_delay_seconds

        # Reset after consuming so delay is one-shot
        self.current_delay_seconds = 0
        return max(0, delay)

    def set_scroll(self, steps: Any):
        try:
            self.current_scroll_steps = max(0, int(steps or 0))
        except Exception:
            self.current_scroll_steps = 0
        logger.info(f"Custom scroll steps set to {self.current_scroll_steps} in Python UI state.")

    def get_and_consume_scroll(self, param_scroll: Any = 0) -> int:
        """Returns the scroll steps and resets it back to 0."""
        steps = 0
        try:
            steps = int(param_scroll or 0)
        except Exception:
            steps = 0
        if steps <= 0:
            steps = self.current_scroll_steps

        # Reset after consuming so scroll is one-shot
        self.current_scroll_steps = 0
        return max(0, steps)

    def dispatch_navigate(
        self, target_url: str, custom_delay_seconds: int = 0, tab_id: str = "", custom_scroll_steps: int = 0
    ):
        """Handles URL bar or link navigation."""
        if self.is_busy:
            self._notify_ui("The system is still processing the previous request.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)
        scroll_steps = self.get_and_consume_scroll(custom_scroll_steps)

        def task():
            self.is_busy = True
            details = []
            if delay > 0:
                details.append(f"{delay}s delay")
            if scroll_steps > 0:
                details.append(f"{scroll_steps} scroll steps")
            msg_suffix = f" (with {' and '.join(details)} on the runner)..." if details else "..."
            msg = f"Preparing and sending to GitHub{msg_suffix}"
            self._set_loading(True, msg)

            def on_status(msg):
                self._update_status(msg)

            res = self.transport.fetch_page(
                target_url=target_url,
                mode="navigate",
                custom_delay_seconds=delay,
                custom_scroll_steps=scroll_steps,
                status_callback=on_status,
            )
            self._handle_fetch_result(res, target_url, tab_id=tab_id)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def dispatch_form_submit(
        self,
        action_url: str,
        method: str,
        content_type: str,
        body_base64: str,
        custom_delay_seconds: int = 0,
        tab_id: str = "",
        custom_scroll_steps: int = 0,
    ):
        """Handles form submission."""
        if self.is_busy:
            self._notify_ui("The previous request is still processing.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)
        scroll_steps = self.get_and_consume_scroll(custom_scroll_steps)

        def task():
            self.is_busy = True
            details = []
            if delay > 0:
                details.append(f"{delay}s delay")
            if scroll_steps > 0:
                details.append(f"{scroll_steps} scroll steps")
            msg_suffix = f" (with {' and '.join(details)} on the runner)..." if details else "..."
            msg = f"Sending form data to GitHub{msg_suffix}"
            self._set_loading(True, msg)

            body_bytes = base64.b64decode(body_base64) if body_base64 else b""

            res = self.transport.fetch_page(
                target_url=action_url,
                mode="form",
                method=method,
                content_type=content_type,
                body_bytes=body_bytes,
                custom_delay_seconds=delay,
                custom_scroll_steps=scroll_steps,
                status_callback=self._update_status,
            )
            self._handle_fetch_result(res, action_url, tab_id=tab_id)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def dispatch_replay(
        self,
        target_url: str,
        actions: List[Dict[str, Any]],
        intercepted_ajax: Optional[Dict[str, Any]] = None,
        custom_delay_seconds: int = 0,
        tab_id: str = "",
        custom_scroll_steps: int = 0,
    ):
        """Handles single or batch replay execution."""
        if self.is_busy:
            self._notify_ui("The previous request is still processing.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)
        scroll_steps = self.get_and_consume_scroll(custom_scroll_steps)

        def task():
            self.is_busy = True
            details = []
            if delay > 0:
                details.append(f"{delay}s delay")
            if scroll_steps > 0:
                details.append(f"{scroll_steps} scroll steps")
            msg_suffix = f" (with {' and '.join(details)})..." if details else "..."
            msg = f"Replaying {len(actions)} actions on the runner{msg_suffix}"
            self._set_loading(True, msg)

            mode = "replay"
            if not actions and intercepted_ajax and intercepted_ajax.get("captured"):
                mode = "ajax_fallback"

            res = self.transport.fetch_page(
                target_url=target_url or self.current_url,
                mode=mode,
                actions=actions,
                intercepted_ajax=intercepted_ajax,
                custom_delay_seconds=delay,
                custom_scroll_steps=scroll_steps,
                status_callback=self._update_status,
            )
            self._handle_fetch_result(res, target_url or self.current_url, tab_id=tab_id)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def _handle_fetch_result(self, result: FetchResult, requested_url: str, tab_id: str = ""):
        if not result.success:
            self._notify_ui(f"Error: {result.error_message}", is_error=True)
            return

        if result.response_type == "file":
            filename = result.meta.get("download_filename") or Path(result.downloaded_file_path or "").name
            msg = f"File downloaded successfully: {filename}"
            self._notify_ui(msg, is_file=True)
            try:
                self.storage_db.add_history(
                    url=requested_url,
                    title=f"File download: {filename}",
                    local_cache_path=str(result.downloaded_file_path or ""),
                )
                self.storage_db.set_page_cache(
                    url=requested_url,
                    hash=result.meta.get("sha256", ""),
                    size=result.meta.get("size", 0),
                    mime_type="application/octet-stream",
                )
            except Exception as e:
                logger.debug(f"Storage DB error for file: {e}")
            return

        # Web page response
        if result.index_html_path:
            p = Path(result.index_html_path)
            # URL path under local server: /cache/<request_id>/extracted/index.html
            rel = p.relative_to(self.transport.cache_base)
            local_frame_url = f"/cache/{rel.as_posix()}"
            final_url = result.meta.get("final_url", requested_url)
            title = result.meta.get("title", final_url)

            # Persist to SQLite Storage Database
            try:
                self.storage_db.add_history(
                    url=final_url,
                    title=title,
                    local_cache_path=str(p),
                )
                self.storage_db.set_page_cache(
                    url=final_url,
                    hash=result.meta.get("sha256", result.meta.get("hash", "")),
                    size=result.meta.get("size", p.stat().st_size if p.exists() else 0),
                    mime_type="text/html",
                )
            except Exception as e:
                logger.debug(f"Storage DB error for page: {e}")

            # Update history
            self.current_url = final_url
            if self.history_index < len(self.history) - 1:
                self.history = self.history[: self.history_index + 1]
            self.history.append({"url": final_url, "local_url": local_frame_url, "title": title})
            self.history_index = len(self.history) - 1

            # Tell shell to load iframe and update address bar (supporting multi-tab)
            self._call_js(
                f"window.shellApp.onPageLoaded('{local_frame_url}', '{final_url}', {json.dumps(title)}, '{tab_id}')"
            )

    # ---------------- Helper UI Communicators ----------------

    def _call_js(self, js_code: str):
        if self.window:
            try:
                self.window.evaluate_js(js_code)
            except Exception as e:
                logger.debug(f"JS evaluation error: {e}")
        self.push_js_event(js_code)

    def _set_loading(self, is_loading: bool, message: str = ""):
        js = f"window.shellApp.setLoading({json.dumps(is_loading)}, {json.dumps(message)})"
        self._call_js(js)

    def _update_status(self, message: str):
        js = f"window.shellApp.updateStatus({json.dumps(message)})"
        self._call_js(js)

    def _notify_ui(self, message: str, is_error: bool = False, is_file: bool = False):
        js = f"window.shellApp.showToast({json.dumps(message)}, {json.dumps(is_error)}, {json.dumps(is_file)})"
        self._call_js(js)

    def open_downloads_folder(self):
        """Opens user's Downloads/GitHubBrowser folder."""
        d = Path(self.config.downloads_dir)
        d.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(d))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:
            self._notify_ui(f"Failed to open folder: {e}", is_error=True)

    def clear_session(self):
        """Clears all session cookies and local storage."""
        self.session_manager.clear_session()
        self._notify_ui("All cookies and session data were cleared.")

    def go_back(self):
        if self.history_index > 0:
            self.history_index -= 1
            entry = self.history[self.history_index]
            self.current_url = entry["url"]
            self._call_js(
                f"window.shellApp.onPageLoaded('{entry['local_url']}', '{entry['url']}', {json.dumps(entry['title'])})"
            )

    def go_forward(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            entry = self.history[self.history_index]
            self.current_url = entry["url"]
            self._call_js(
                f"window.shellApp.onPageLoaded('{entry['local_url']}', '{entry['url']}', {json.dumps(entry['title'])})"
            )

    def reload(self):
        if self.current_url and self.current_url != "about:blank":
            self.dispatch_navigate(self.current_url)

    def go_home(self):
        self._call_js("window.shellApp.onPageLoaded('/start', '', 'Offline Start')")

    # ---------------- HTML UI Templates ----------------
    # ---------------- HTML UI Templates ----------------
    def _generate_shell_html(self) -> str:
        """Generates pure OLED black UI shell with white buttons and complete onPageLoaded handler."""
        return generate_shell_html(self.config)

    def _generate_start_page_html(self) -> str:
        """Generates the fixed scroll-free home screen with NTTM branding, the inverted halftone dot backdrop, and clean dark UI."""
        return generate_start_page_html(self.config, self.session_manager)
