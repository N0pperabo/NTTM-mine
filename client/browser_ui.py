import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

import webview

from .config import BrowserConfig
from .session_manager import SessionManager
from .transport import FetchResult, GitHubTransport

logger = logging.getLogger("BrowserUI")


class LocalAssetHandler(SimpleHTTPRequestHandler):
    """
    HTTP server serving static assets, cached offline web pages,
    and the integrated browser shell.
    """

    cache_dir: Path = Path.home() / ".gh_offline_browser" / "cache"
    shell_html: str = ""
    start_html: str = ""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html", "/shell"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.shell_html.encode("utf-8"))
            return

        if path == "/start":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.start_html.encode("utf-8"))
            return

        if path.startswith("/cache/"):
            rel_path = path[len("/cache/") :]
            file_path = self.cache_dir / rel_path
            if file_path.exists() and file_path.is_file():
                self.send_response(200)
                # Guess mime type
                mime = "text/html; charset=utf-8" if file_path.suffix == ".html" else "application/octet-stream"
                if file_path.suffix == ".css":
                    mime = "text/css; charset=utf-8"
                elif file_path.suffix in (".js", ".mjs"):
                    mime = "application/javascript"
                elif file_path.suffix in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                    mime = f"image/{file_path.suffix.lstrip('.')}"
                self.send_header("Content-Type", mime)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self' 'unsafe-inline' data: blob: http://127.0.0.1:*; connect-src 'none'; form-action 'none';",
                )
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
                return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        # Silence standard HTTP logs to avoid clutter
        pass


class OfflineBrowserUI:
    """
    PyWebView Shell, Action Queue UI, AJAX Trap & Deep DOM Interceptor.
    """

    def __init__(self, transport: GitHubTransport, session_manager: SessionManager):
        self.transport = transport
        self.config = transport.config
        self.session_manager = session_manager
        self.window = None
        self.server_port = 0
        self.server_thread = None

        # History navigation
        self.history: List[Dict[str, str]] = []
        self.history_index: int = -1

        # Current navigation state
        self.current_url: str = "about:blank"
        self.is_busy: bool = False
        self.is_batch_mode: bool = False
        self.action_queue: List[Dict[str, Any]] = []
        self.current_delay_seconds: int = 0

    def start_local_server(self) -> int:
        """Starts the local HTTP server on a free port."""
        LocalAssetHandler.cache_dir = self.transport.cache_base
        LocalAssetHandler.shell_html = self._generate_shell_html()
        LocalAssetHandler.start_html = self._generate_start_page_html()

        server = HTTPServer(("127.0.0.1", 0), LocalAssetHandler)
        self.server_port = server.server_port

        def run_server():
            server.serve_forever()

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Local UI server running at http://127.0.0.1:{self.server_port}/")
        return self.server_port

    def launch(self):
        """Launches the PyWebView window."""
        port = self.start_local_server()
        url = f"http://127.0.0.1:{port}/"

        api = WebviewApiBridge(self)

        self.window = webview.create_window(
            title="مرورگر آفلاین گیت‌هاب (GitHub Offline Browser)",
            url=url,
            js_api=api,
            width=1366,
            height=850,
            min_size=(1024, 700),
            background_color="#0f172a",
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

    def dispatch_navigate(self, target_url: str, custom_delay_seconds: int = 0):
        """Handles URL bar or link navigation."""
        if self.is_busy:
            self._notify_ui("سیستم در حال حاضر مشغول پردازش درخواست قبلی است.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)

        def task():
            self.is_busy = True
            msg = f"در حال آماده‌سازی و ارسال به گیت‌هاب (با تأخیر {delay} ثانیه‌ای در رانر)..." if delay > 0 else "در حال آماده‌سازی و ارسال به گیت‌هاب..."
            self._set_loading(True, msg)

            def on_status(msg):
                self._update_status(msg)

            res = self.transport.fetch(
                target_url=target_url,
                mode="navigate",
                custom_delay_seconds=delay,
                status_callback=on_status,
            )
            self._handle_fetch_result(res, target_url)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def dispatch_form_submit(
        self, action_url: str, method: str, content_type: str, body_base64: str, custom_delay_seconds: int = 0
    ):
        """Handles form submission."""
        if self.is_busy:
            self._notify_ui("درخواست قبلی هنوز در حال پردازش است.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)

        def task():
            self.is_busy = True
            msg = f"در حال ارسال اطلاعات فرم به گیت‌هاب (با تأخیر {delay} ثانیه‌ای در رانر)..." if delay > 0 else "در حال ارسال اطلاعات فرم به گیت‌هاب..."
            self._set_loading(True, msg)

            body_bytes = base64.b64decode(body_base64) if body_base64 else b""

            res = self.transport.fetch(
                target_url=action_url,
                mode="form",
                method=method,
                content_type=content_type,
                body_bytes=body_bytes,
                custom_delay_seconds=delay,
                status_callback=self._update_status,
            )
            self._handle_fetch_result(res, action_url)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def dispatch_replay(
        self,
        target_url: str,
        actions: List[Dict[str, Any]],
        intercepted_ajax: Optional[Dict[str, Any]] = None,
        custom_delay_seconds: int = 0,
    ):
        """Handles single or batch replay execution."""
        if self.is_busy:
            self._notify_ui("درخواست قبلی در حال پردازش است.")
            return

        delay = self.get_and_consume_delay(custom_delay_seconds)

        def task():
            self.is_busy = True
            msg = f"در حال بازپخش {len(actions)} اکشن در رانر (با تأخیر {delay} ثانیه‌ای)..." if delay > 0 else f"در حال بازپخش {len(actions)} اکشن در رانر..."
            self._set_loading(True, msg)

            mode = "replay"
            if not actions and intercepted_ajax and intercepted_ajax.get("captured"):
                mode = "ajax_fallback"

            res = self.transport.fetch(
                target_url=target_url or self.current_url,
                mode=mode,
                actions=actions,
                intercepted_ajax=intercepted_ajax,
                custom_delay_seconds=delay,
                status_callback=self._update_status,
            )
            self._handle_fetch_result(res, target_url or self.current_url)
            self.is_busy = False
            self._set_loading(False)

        threading.Thread(target=task, daemon=True).start()

    def _handle_fetch_result(self, result: FetchResult, requested_url: str):
        if not result.success:
            self._notify_ui(f"خطا: {result.error_message}", is_error=True)
            return

        if result.response_type == "file":
            filename = result.meta.get("download_filename") or Path(result.downloaded_file_path or "").name
            msg = f"فایل با موفقیت دانلود شد: {filename}"
            self._notify_ui(msg, is_file=True)
            return

        # Web page response
        if result.index_html_path:
            p = Path(result.index_html_path)
            # URL path under local server: /cache/<request_id>/extracted/index.html
            rel = p.relative_to(self.transport.cache_base)
            local_frame_url = f"/cache/{rel.as_posix()}"
            final_url = result.meta.get("final_url", requested_url)
            title = result.meta.get("title", final_url)

            # Update history
            self.current_url = final_url
            if self.history_index < len(self.history) - 1:
                self.history = self.history[: self.history_index + 1]
            self.history.append({"url": final_url, "local_url": local_frame_url, "title": title})
            self.history_index = len(self.history) - 1

            # Tell shell to load iframe and update address bar
            self._call_js(
                f"window.shellApp.onPageLoaded('{local_frame_url}', '{final_url}', {json.dumps(title)})"
            )

    # ---------------- Helper UI Communicators ----------------

    def _call_js(self, js_code: str):
        if self.window:
            try:
                self.window.evaluate_js(js_code)
            except Exception as e:
                logger.debug(f"JS evaluation error: {e}")

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
            self._notify_ui(f"باز کردن پوشه با خطا مواجه شد: {e}", is_error=True)

    def clear_session(self):
        """Clears all session cookies and local storage."""
        self.session_manager.clear_session()
        self._notify_ui("تمام کوکی‌ها و اطلاعات نشست با موفقیت پاکسازی شدند.")

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
        self._call_js("window.shellApp.onPageLoaded('/start', '', 'صفحه شروع آفلاین')")

    # ---------------- HTML UI Templates ----------------

    def _generate_shell_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitHub Offline Browser</title>
  <style>
    :root {
      --bg-dark: #0f172a;
      --bg-card: #1e293b;
      --bg-card-hover: #334155;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --border: #334155;
      --toolbar-height: 60px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: system-ui, -apple-system, 'Segoe UI', Tahoma, sans-serif; }
    body { background: var(--bg-dark); color: var(--text); overflow: hidden; display: flex; flex-direction: column; height: 100vh; }

    /* Top Toolbar */
    .toolbar {
      height: var(--toolbar-height);
      background: var(--bg-card);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 16px;
      z-index: 100;
    }
    .btn-nav {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text);
      width: 36px;
      height: 36px;
      border-radius: 8px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 16px;
      transition: all 0.2s;
    }
    .btn-nav:hover { background: var(--bg-card-hover); border-color: var(--primary); }

    /* Address Bar */
    .url-bar-container {
      flex: 1;
      display: flex;
      align-items: center;
      background: var(--bg-dark);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 4px 12px;
      transition: border-color 0.2s;
    }
    .url-bar-container:focus-within { border-color: var(--primary); box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2); }
    .url-input {
      flex: 1;
      background: transparent;
      border: none;
      color: var(--text);
      font-size: 14px;
      outline: none;
      direction: ltr;
      text-align: left;
      padding: 6px;
    }
    .btn-go {
      background: var(--primary);
      color: #fff;
      border: none;
      padding: 6px 14px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      transition: background 0.2s;
    }
    .btn-go:hover { background: var(--primary-hover); }

    /* Mode Toggle */
    .mode-toggle {
      display: flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-dark);
      padding: 4px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      cursor: pointer;
      font-size: 13px;
      user-select: none;
    }
    .mode-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--success); }
    .mode-toggle.batch .mode-dot { background: var(--warning); }

    /* Daemon Fast Mode Toggle */
    .daemon-btn {
      display: flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-dark);
      padding: 4px 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      cursor: pointer;
      font-size: 13px;
      user-select: none;
      transition: all 0.2s;
    }
    .daemon-btn.active {
      background: rgba(16, 185, 129, 0.15);
      border-color: var(--success);
      color: #34d399;
    }
    .daemon-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #64748b;
      transition: background 0.3s;
    }
    .daemon-btn.active .daemon-dot {
      background: var(--success);
      box-shadow: 0 0 8px #10b981;
      animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

    /* Custom Delay Controls */
    .delay-container {
      display: flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-dark);
      padding: 4px 8px;
      border-radius: 8px;
      border: 1px solid var(--border);
      transition: all 0.2s;
    }
    .delay-container.active {
      border-color: #f59e0b;
      background: rgba(245, 158, 11, 0.15);
    }
    .delay-btn {
      display: flex;
      align-items: center;
      gap: 5px;
      cursor: pointer;
      font-size: 13px;
      user-select: none;
      color: var(--text-muted);
      transition: color 0.2s;
    }
    .delay-container.active .delay-btn {
      color: #fbbf24;
      font-weight: 600;
    }
    .delay-input-box {
      display: flex;
      align-items: center;
      gap: 4px;
      border-right: 1px solid var(--border);
      padding-right: 6px;
      margin-right: 2px;
    }
    .delay-input-box input {
      width: 44px;
      background: var(--bg-card);
      border: 1px solid #475569;
      color: #fbbf24;
      padding: 2px 4px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: bold;
      text-align: center;
      outline: none;
      direction: ltr;
    }

    /* Quick Action Buttons */
    .action-btn {
      background: var(--bg-dark);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 10px;
      border-radius: 8px;
      font-size: 12px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.2s;
    }
    .action-btn:hover { background: var(--bg-card-hover); }

    /* Action Queue Bar (Batch Mode) */
    .queue-bar {
      background: #182234;
      border-bottom: 1px solid var(--border);
      padding: 8px 16px;
      display: none;
      align-items: center;
      gap: 12px;
      font-size: 13px;
    }
    .queue-bar.active { display: flex; }
    .queue-items {
      flex: 1;
      display: flex;
      gap: 8px;
      overflow-x: auto;
      white-space: nowrap;
      padding-bottom: 4px;
    }
    .queue-badge {
      background: var(--bg-card);
      border: 1px solid #475569;
      padding: 4px 10px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
    }
    .btn-run-batch {
      background: var(--success);
      color: #fff;
      border: none;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 600;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .btn-run-batch:hover { opacity: 0.9; }

    /* Loading / Status Bar */
    .status-bar {
      background: #1e3a8a;
      color: #bfdbfe;
      padding: 6px 16px;
      font-size: 12px;
      display: none;
      align-items: center;
      gap: 10px;
    }
    .status-bar.active { display: flex; }
    .spinner {
      width: 14px;
      height: 14px;
      border: 2px solid #bfdbfe;
      border-top-color: transparent;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* Frame Container */
    .frame-container { flex: 1; position: relative; background: #fff; }
    iframe { width: 100%; height: 100%; border: none; }

    /* Toast Notification */
    .toast {
      position: fixed;
      bottom: 20px;
      left: 20px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 12px 20px;
      border-radius: 10px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.4);
      z-index: 1000;
      display: none;
      align-items: center;
      gap: 12px;
      max-width: 450px;
      animation: slideUp 0.3s ease;
    }
    .toast.error { border-color: var(--danger); color: #fca5a5; }
    .toast.file { border-color: var(--success); color: #86efac; }
    @keyframes slideUp { from { transform: translateY(30px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

    /* Settings Modal */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.7);
      backdrop-filter: blur(4px);
      z-index: 2000;
      display: none;
      align-items: center;
      justify-content: center;
    }
    .modal-overlay.open { display: flex; }
    .modal {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 14px;
      width: 520px;
      max-width: 90%;
      padding: 24px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.6);
    }
    .modal h2 { margin-bottom: 16px; font-size: 18px; color: var(--text); }
    .form-group { margin-bottom: 14px; }
    .form-group label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 5px; }
    .form-group input {
      width: 100%;
      background: var(--bg-dark);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 13px;
      outline: none;
      direction: ltr;
      text-align: left;
    }
    .form-group input:focus { border-color: var(--primary); }
    .modal-buttons { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
  </style>
</head>
<body>

  <!-- Top Toolbar -->
  <div class="toolbar">
    <button class="btn-nav" title="بازگشت" onclick="window.pywebview.api.go_back()">❮</button>
    <button class="btn-nav" title="جلو" onclick="window.pywebview.api.go_forward()">❯</button>
    <button class="btn-nav" title="تازه‌سازی" onclick="window.pywebview.api.reload()">↻</button>
    <button class="btn-nav" title="صفحه شروع" onclick="window.pywebview.api.go_home()">🏠</button>

    <div class="url-bar-container">
      <input type="text" id="url-bar" class="url-input" placeholder="آدرس سایت (مثال: https://wikipedia.org) یا کلمه جستجو..." />
      <button class="btn-go" onclick="window.shellApp.onGoClick()">ارسال / Go</button>
    </div>

    <!-- Mode Toggle Button -->
    <div id="mode-toggle" class="mode-toggle" onclick="window.shellApp.toggleMode()">
      <div class="mode-dot"></div>
      <span id="mode-label">حالت مستقیم</span>
    </div>

    <!-- Daemon Fast Mode Toggle Button -->
    <div id="daemon-btn" class="daemon-btn" onclick="window.shellApp.toggleDaemon()" title="کلیک برای روشن/خاموش کردن رانر دائمی (پاسخ فوق سریع ۲-۵ ثانیه‌ای)">
      <div class="daemon-dot"></div>
      <span id="daemon-label">⚡ رانر زنده: در حال بررسی...</span>
    </div>

    <!-- Custom Delay Toggle (Auto-resets after each fetch) -->
    <div id="delay-container" class="delay-container">
      <div id="delay-btn" class="delay-btn" onclick="window.shellApp.toggleDelay()" title="تأخیر اختصاصی در رانر برای لود کامل کپچا و صفحات سنگین (پیش‌فرض ۱۵ ثانیه)">
        <span>⏱️</span>
        <span id="delay-status-text">تأخیر: خاموش</span>
      </div>
      <div id="delay-input-box" class="delay-input-box" style="display: none;">
        <input type="number" id="delay-seconds-input" min="1" max="180" value="15" title="ثانیه تأخیر در رانر" oninput="window.shellApp.onDelayChange()" onchange="window.shellApp.onDelayChange()" />
        <span style="font-size: 11px; color: #94a3b8;">ثانیه</span>
      </div>
    </div>

    <button class="action-btn" title="پوشه فایل‌های دانلود شده" onclick="window.pywebview.api.open_downloads_folder()">📁 دانلودها</button>
    <button class="action-btn" title="پاکسازی کوکی‌ها و سشن ذخیره شده" onclick="window.pywebview.api.clear_session()">🧹 پاکسازی نشست</button>
    <button class="action-btn" title="تنظیمات مخزن و توکن گیت‌هاب" onclick="window.shellApp.openSettings()">⚙️ تنظیمات</button>
  </div>

  <!-- Action Queue Bar (Batch Mode) -->
  <div id="queue-bar" class="queue-bar">
    <span style="font-weight: 600; color: var(--warning);">صف اکشن‌ها:</span>
    <div id="queue-items" class="queue-items">
      <span style="color: var(--text-muted); font-size: 12px;">روی المنت‌های صفحه کلیک یا تایپ کنید تا به صف اضافه شوند...</span>
    </div>
    <button class="btn-run-batch" onclick="window.shellApp.runBatch()">🚀 اجرای زنجیره در گیت‌هاب</button>
    <button class="action-btn" onclick="window.shellApp.undoAction()">↩️ واگرد</button>
    <button class="action-btn" onclick="window.shellApp.clearQueue()">🗑️ پاکسازی</button>
  </div>

  <!-- Loading & Status Bar -->
  <div id="status-bar" class="status-bar">
    <div class="spinner"></div>
    <span id="status-text">در حال ارسال درخواست به گیت‌هاب...</span>
  </div>

  <!-- Content Frame -->
  <div class="frame-container">
    <iframe id="content-frame" src="/start" sandbox="allow-same-origin allow-scripts allow-forms"></iframe>
  </div>

  <!-- Toast Notification -->
  <div id="toast" class="toast">
    <span id="toast-text">پیام</span>
  </div>

  <!-- Settings Modal -->
  <div id="settings-modal" class="modal-overlay">
    <div class="modal">
      <h2>تنظیمات اتصال به گیت‌هاب</h2>
      <div class="form-group">
        <label>توکن دسترسی شخصی گیت‌هاب (Personal Access Token - PAT):</label>
        <input type="password" id="cfg-token" placeholder="ghp_... با دسترسی repo و workflow" />
      </div>
      <div class="form-group">
        <label>نام مخزن گیت‌هاب (GitHub Repository):</label>
        <input type="text" id="cfg-repo" placeholder="owner/repo-name" />
      </div>
      <div class="form-group">
        <label>نام شاخه اصلی (Branch):</label>
        <input type="text" id="cfg-branch" value="main" />
      </div>
      <div class="form-group">
        <label>موتور جستجوی پیش‌فرض:</label>
        <input type="text" id="cfg-search" value="https://www.google.com/search?q={query}" />
      </div>
      <div class="form-group">
        <label>کلید رمزنگاری سرتاسری (AES-256-GCM) - ویژه مخازن پابلیک:</label>
        <input type="password" id="cfg-enc" placeholder="کلید دلخواه (باید با Secret مخزن به نام ENCRYPTION_KEY یکسان باشد)" />
      </div>
      <div class="modal-buttons">
        <button class="action-btn" onclick="window.shellApp.closeSettings()">انصراف</button>
        <button class="btn-go" onclick="window.shellApp.saveSettings()">ذخیره تنظیمات</button>
      </div>
    </div>
  </div>

  <script>
    class ShellApp {
      constructor() {
        this.urlBar = document.getElementById('url-bar');
        this.frame = document.getElementById('content-frame');
        this.statusBar = document.getElementById('status-bar');
        this.statusText = document.getElementById('status-text');
        this.queueBar = document.getElementById('queue-bar');
        this.queueItems = document.getElementById('queue-items');
        this.modeToggle = document.getElementById('mode-toggle');
        this.modeLabel = document.getElementById('mode-label');
        this.toast = document.getElementById('toast');

        this.isBatchMode = false;
        this.actionQueue = [];
        this.delayEnabled = false;
        this.currentLiveUrl = '';

        this.init();
      }

      init() {
        this.urlBar.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            this.onGoClick();
          }
        });

        // Inject interceptor whenever iframe finishes loading
        this.frame.addEventListener('load', () => {
          this.injectDeepInterceptor();
        });

        // Check daemon worker status periodically
        setTimeout(() => this.pollDaemonStatus(), 1000);
        setInterval(() => this.pollDaemonStatus(), 12000);
      }

      pollDaemonStatus() {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_daemon_status) {
          window.pywebview.api.get_daemon_status().then((res) => {
            this.updateDaemonUI(res.alive);
          }).catch(() => {});
        }
      }

      toggleDaemon() {
        this.showToast('در حال ارسال دستور تغییر وضعیت رانر زنده...');
        window.pywebview.api.toggle_daemon().then((res) => {
          this.updateDaemonUI(res.alive);
          this.showToast(res.msg);
        }).catch((e) => {
          this.showToast('خطا در ارتباط با رانر زنده: ' + e, true);
        });
      }

      updateDaemonUI(isAlive) {
        const btn = document.getElementById('daemon-btn');
        const label = document.getElementById('daemon-label');
        if (!btn || !label) return;
        if (isAlive) {
          btn.classList.add('active');
          label.textContent = '⚡ رانر زنده: روشن (Ultra Fast)';
        } else {
          btn.classList.remove('active');
          label.textContent = '⚡ رانر زنده: خاموش';
        }
      }

      onGoClick() {
        const val = this.urlBar.value.trim();
        if (val) {
          window.pywebview.api.navigate(val, this.getDelaySeconds());
        }
      }

      toggleMode() {
        this.isBatchMode = !this.isBatchMode;
        if (this.isBatchMode) {
          this.modeToggle.classList.add('batch');
          this.modeLabel.textContent = 'حالت ضبط زنجیره‌ای (Batch)';
          this.queueBar.classList.add('active');
        } else {
          this.modeToggle.classList.remove('batch');
          this.modeLabel.textContent = 'حالت مستقیم';
          this.queueBar.classList.remove('active');
        }
        this.renderQueue();
      }

      addAction(action) {
        this.actionQueue.push(action);
        this.renderQueue();
        this.showToast(`گام ${this.actionQueue.length} به صف افزوده شد: ${action.type}`);
      }

      undoAction() {
        if (this.actionQueue.length > 0) {
          this.actionQueue.pop();
          this.renderQueue();
        }
      }

      clearQueue() {
        this.actionQueue = [];
        this.renderQueue();
      }

      renderQueue() {
        if (this.actionQueue.length === 0) {
          this.queueItems.innerHTML = '<span style="color: var(--text-muted); font-size: 12px;">روی المنت‌های صفحه کلیک یا تایپ کنید تا به صف اضافه شوند...</span>';
          return;
        }

        this.queueItems.innerHTML = '';
        this.actionQueue.forEach((act, idx) => {
          const badge = document.createElement('div');
          badge.className = 'queue-badge';
          const title = act.text_hint || act.selector || act.key || act.type;
          badge.innerHTML = `<strong>${idx + 1}.</strong> ${act.type} (${title.slice(0, 25)})`;
          this.queueItems.appendChild(badge);
        });
      }

      toggleDelay() {
        this.delayEnabled = !this.delayEnabled;
        const container = document.getElementById('delay-container');
        const statusText = document.getElementById('delay-status-text');
        const inputBox = document.getElementById('delay-input-box');
        const input = document.getElementById('delay-seconds-input');
        const sec = parseInt(input ? input.value : 15, 10) || 15;

        if (this.delayEnabled) {
          container.classList.add('active');
          statusText.textContent = `تأخیر: ${sec}s`;
          inputBox.style.display = 'flex';
          this.showToast(`⏱️ تأخیر ${sec} ثانیه‌ای فعال شد (پس از دریافت داده‌ها خاموش خواهد شد)`);
          if (window.pywebview && window.pywebview.api && window.pywebview.api.set_delay) {
            window.pywebview.api.set_delay(sec);
          }
        } else {
          this.resetDelay();
        }
      }

      onDelayChange() {
        if (this.delayEnabled) {
          const sec = this.getDelaySeconds();
          const statusText = document.getElementById('delay-status-text');
          if (statusText) statusText.textContent = `تأخیر: ${sec}s`;
          if (window.pywebview && window.pywebview.api && window.pywebview.api.set_delay) {
            window.pywebview.api.set_delay(sec);
          }
        }
      }

      resetDelay() {
        this.delayEnabled = false;
        const container = document.getElementById('delay-container');
        const statusText = document.getElementById('delay-status-text');
        const inputBox = document.getElementById('delay-input-box');
        if (container) container.classList.remove('active');
        if (statusText) statusText.textContent = 'تأخیر: خاموش';
        if (inputBox) inputBox.style.display = 'none';
        if (window.pywebview && window.pywebview.api && window.pywebview.api.set_delay) {
          window.pywebview.api.set_delay(0);
        }
      }

      getDelaySeconds() {
        if (!this.delayEnabled) return 0;
        const input = document.getElementById('delay-seconds-input');
        return parseInt(input ? input.value : 15, 10) || 15;
      }

      runBatch() {
        if (this.actionQueue.length === 0) {
          this.showToast('صف اکشن‌ها خالی است.', true);
          return;
        }
        window.pywebview.api.run_batch_actions(this.currentLiveUrl, this.actionQueue, this.getDelaySeconds());
        this.clearQueue();
      }

      setLoading(isLoading, msg) {
        if (isLoading) {
          this.statusBar.classList.add('active');
          this.statusText.textContent = msg || 'در حال پردازش...';
        } else {
          this.statusBar.classList.remove('active');
        }
      }

      updateStatus(msg) {
        this.statusText.textContent = msg;
      }

      onPageLoaded(localUrl, targetUrl, title) {
        this.resetDelay();
        this.currentLiveUrl = targetUrl;
        if (targetUrl) {
          this.urlBar.value = targetUrl;
        }
        this.frame.src = localUrl;
        if (title) {
          document.title = title + ' - GitHub Offline Browser';
        }
      }

      showToast(msg, isError = false, isFile = false) {
        this.toast.className = 'toast' + (isError ? ' error' : '') + (isFile ? ' file' : '');
        this.toast.innerHTML = isFile ? `📁 ${msg} <button style="margin-right:10px;padding:3px 8px;" onclick="window.pywebview.api.open_downloads_folder()">نمایش در پوشه</button>` : msg;
        this.toast.style.display = 'flex';
        setTimeout(() => {
          this.toast.style.display = 'none';
        }, 5000);
      }

      // Settings Modal
      openSettings() {
        window.pywebview.api.get_config().then((cfg) => {
          document.getElementById('cfg-token').value = cfg.github_token || '';
          document.getElementById('cfg-repo').value = cfg.github_repo || '';
          document.getElementById('cfg-branch').value = cfg.branch || 'main';
          document.getElementById('cfg-search').value = cfg.default_search_url || '';
          document.getElementById('cfg-enc').value = cfg.encryption_key || '';
          document.getElementById('settings-modal').classList.add('open');
        });
      }

      closeSettings() {
        document.getElementById('settings-modal').classList.remove('open');
      }

      saveSettings() {
        const token = document.getElementById('cfg-token').value.trim();
        const repo = document.getElementById('cfg-repo').value.trim();
        const branch = document.getElementById('cfg-branch').value.trim() || 'main';
        const searchUrl = document.getElementById('cfg-search').value.trim();
        const encKey = document.getElementById('cfg-enc').value.trim();

        window.pywebview.api.save_config(token, repo, branch, searchUrl, encKey).then((ok) => {
          if (ok) {
            this.showToast('تنظیمات با موفقیت ذخیره شد.');
            this.closeSettings();
          } else {
            this.showToast('خطا در ذخیره تنظیمات.', true);
          }
        });
      }

      // Deep Interceptor injected into iframe
      injectDeepInterceptor() {
        try {
          const doc = this.frame.contentDocument;
          const win = this.frame.contentWindow;
          if (!doc || !win) return;

          // Only intercept external cached web pages under /cache/
          // NEVER intercept internal pages like /start or about:blank!
          try {
            const loc = win.location;
            if (!loc || !loc.pathname || !loc.pathname.startsWith('/cache/')) {
              return;
            }
          } catch (e) {
            return;
          }

          // Helper: Unique Descriptor Builder
          function buildDescriptor(el) {
            let selector = '';
            if (el.id) {
              selector = '#' + el.id;
            } else if (el.getAttribute('name')) {
              selector = `${el.tagName.toLowerCase()}[name="${el.getAttribute('name')}"]`;
            } else if (el.className && typeof el.className === 'string') {
              const cls = el.className.trim().split(/\\s+/)[0];
              if (cls) selector = `${el.tagName.toLowerCase()}.${cls}`;
            }
            if (!selector) {
              selector = el.tagName.toLowerCase();
            }

            // Simple XPath
            let xpath = '//' + el.tagName.toLowerCase();
            if (el.id) {
              xpath += `[@id='${el.id}']`;
            }

            const textHint = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().slice(0, 50);
            const rect = el.getBoundingClientRect();
            const x = Math.round(rect.left + rect.width / 2);
            const y = Math.round(rect.top + rect.height / 2);

            return { selector, xpath, text_hint: textHint, x, y };
          }

          // Buffer for pending input values
          win.__pendingInputs = win.__pendingInputs || {};

          // 1. Input tracking (input, change in Capture phase)
          doc.addEventListener('input', (e) => {
            const el = e.target;
            if (['INPUT', 'TEXTAREA'].includes(el.tagName)) {
              const desc = buildDescriptor(el);
              desc.type = 'fill';
              desc.value = el.value;
              win.__pendingInputs[desc.selector] = desc;

              if (window.shellApp.isBatchMode) {
                window.shellApp.addAction(desc);
              }
            }
          }, true);

          doc.addEventListener('change', (e) => {
            const el = e.target;
            if (el.tagName === 'SELECT') {
              const desc = buildDescriptor(el);
              desc.type = 'select';
              desc.value = el.value;
              win.__pendingInputs[desc.selector] = desc;
              if (window.shellApp.isBatchMode) {
                window.shellApp.addAction(desc);
              }
            } else if (el.type === 'checkbox' || el.type === 'radio') {
              const desc = buildDescriptor(el);
              desc.type = 'check';
              desc.checked = el.checked;
              win.__pendingInputs[desc.selector] = desc;
              if (window.shellApp.isBatchMode) {
                window.shellApp.addAction(desc);
              }
            }
          }, true);

          // 2. Link & Click Interception (Capture phase)
          doc.addEventListener('click', (e) => {
            const link = e.target.closest('a');
            if (link && link.href) {
              e.preventDefault();
              e.stopPropagation();
              const fullUrl = link.href;

              if (window.shellApp.isBatchMode) {
                const desc = buildDescriptor(link);
                desc.type = 'click';
                window.shellApp.addAction(desc);
              } else {
                window.pywebview.api.navigate(fullUrl, window.shellApp.getDelaySeconds());
              }
              return;
            }

            // Clickable buttons or SPA triggers
            const btn = e.target.closest('button, [role="button"], input[type="submit"], input[type="button"], summary');
            if (btn) {
              e.preventDefault();
              e.stopPropagation();
              const desc = buildDescriptor(btn);
              desc.type = 'click';

              if (window.shellApp.isBatchMode) {
                window.shellApp.addAction(desc);
              } else {
                // Collect any buffered inputs + this click
                const actions = Object.values(win.__pendingInputs);
                actions.push(desc);
                win.__pendingInputs = {};
                window.pywebview.api.dispatch_replay(window.shellApp.currentLiveUrl, actions, null, window.shellApp.getDelaySeconds());
              }
            }
          }, true);

          // 3. Form Submission Interception
          doc.addEventListener('submit', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const form = e.target;
            const actionUrl = form.action || window.shellApp.currentLiveUrl;
            const method = (form.method || 'GET').toUpperCase();

            // Collect inputs
            const formData = new FormData(form);
            const params = new URLSearchParams();
            for (const [k, v] of formData.entries()) {
              params.append(k, v);
            }

            if (window.shellApp.isBatchMode) {
              const desc = buildDescriptor(form);
              desc.type = 'click';
              window.shellApp.addAction(desc);
            } else {
              if (method === 'GET') {
                const delim = actionUrl.includes('?') ? '&' : '?';
                window.pywebview.api.navigate(actionUrl + delim + params.toString(), window.shellApp.getDelaySeconds());
              } else {
                const bodyStr = params.toString();
                const bodyB64 = btoa(unescape(encodeURIComponent(bodyStr)));
                window.pywebview.api.submit_form(actionUrl, method, 'application/x-www-form-urlencoded', bodyB64, window.shellApp.getDelaySeconds());
              }
            }
          }, true);

          // 4. AJAX Trap (window.fetch and XMLHttpRequest)
          const origFetch = win.fetch;
          win.fetch = async function(resource, init = {}) {
            const url = typeof resource === 'string' ? resource : resource.url;
            const method = (init.method || 'GET').toUpperCase();
            let bodyB64 = '';
            if (init.body && typeof init.body === 'string') {
              try { bodyB64 = btoa(unescape(encodeURIComponent(init.body))); } catch {}
            }
            const ajaxData = {
              captured: true,
              url: new URL(url, win.location.href).href,
              method: method,
              headers: init.headers || {},
              body_base64: bodyB64
            };
            if (!window.shellApp.isBatchMode) {
              window.pywebview.api.dispatch_replay(window.shellApp.currentLiveUrl, [], ajaxData);
            }
            return new Response(JSON.stringify({ offline: true }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' }
            });
          };

        } catch (err) {
          console.warn('Deep interceptor injection notice:', err);
        }
      }
    }

    window.shellApp = new ShellApp();
  </script>
</body>
</html>
"""

    def _generate_start_page_html(self) -> str:
        token_status = "✅ تنظیم شده" if self.config.github_token else "❌ تنظیم نشده"
        repo_status = self.config.github_repo if self.config.github_repo else "❌ تنظیم نشده"
        cookies_count = len(self.session_manager.cookies)

        return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <title>صفحه شروع مرورگر آفلاین گیت‌هاب</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; margin: 0; }}
    .container {{ max-width: 850px; margin: 0 auto; }}
    .header {{ text-align: center; margin-bottom: 35px; }}
    h1 {{ font-size: 28px; color: #3b82f6; margin-bottom: 8px; }}
    p.lead {{ color: #94a3b8; font-size: 15px; line-height: 1.6; }}
    
    /* Search Box */
    .search-card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 14px;
      padding: 24px;
      margin-bottom: 30px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.3);
    }}
    .search-input-group {{
      display: flex;
      gap: 10px;
      margin-bottom: 16px;
    }}
    .search-input-group input {{
      flex: 1;
      background: #0f172a;
      border: 1px solid #475569;
      color: #fff;
      padding: 12px 16px;
      border-radius: 10px;
      font-size: 15px;
      outline: none;
      direction: rtl;
    }}
    .search-input-group input:focus {{ border-color: #3b82f6; }}
    .btn-search {{
      background: #3b82f6;
      color: #fff;
      border: none;
      padding: 0 24px;
      border-radius: 10px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
    }}
    .btn-search:hover {{ background: #2563eb; }}

    /* Quick Links */
    .quick-links {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }}
    .quick-link {{
      background: #0f172a;
      border: 1px solid #334155;
      color: #cbd5e1;
      padding: 8px 16px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .quick-link:hover {{ background: #334155; color: #fff; border-color: #3b82f6; }}

    /* Guide & Status */
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 20px;
      font-size: 13px;
      line-height: 1.7;
      color: #cbd5e1;
    }}
    .card h3 {{ color: #38bdf8; margin-top: 0; margin-bottom: 10px; font-size: 16px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; }}
    .badge-ok {{ background: rgba(16, 185, 129, 0.2); color: #34d399; }}
    .badge-warn {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>مرورگر آفلاین گیت‌هاب</h1>
      <p class="lead">وب‌گردی، بازپخش تعاملات SPA، حفظ سشن‌ها و دانلود فایل‌های حجیم ۵۰ مگابایتی فقط از طریق GitHub</p>
    </div>

    <!-- Search Section -->
    <div class="search-card">
      <div class="search-input-group">
        <input type="text" id="start-query" placeholder="جستجو در اینترنت یا وارد کردن نشانی کامل سایت..." onkeydown="if(event.key==='Enter') doSearch()" />
        <button class="btn-search" onclick="doSearch()">جستجو در وب</button>
      </div>
      <div class="quick-links">
        <a class="quick-link" onclick="quickNav('https://www.google.com')">🔍 گوگل (Google)</a>
        <a class="quick-link" onclick="quickNav('https://duckduckgo.com')">🦆 داک‌داک‌گو (DuckDuckGo)</a>
        <a class="quick-link" onclick="quickNav('https://fa.wikipedia.org')">📖 ویکی‌پدیا فارسی</a>
        <a class="quick-link" onclick="quickNav('https://github.com/explore')">🐙 گیت‌هاب (GitHub)</a>
        <a class="quick-link" onclick="quickNav('https://stackoverflow.com')">💻 استک اورفلو</a>
      </div>
    </div>

    <!-- Info Grid -->
    <div class="grid">
      <div class="card">
        <h3>راهنمای دو حالت کارکرد</h3>
        <p><strong>۱. حالت مستقیم (Direct Mode):</strong> با کلیک روی هر لینک، دکمه یا ارسال فرم، تغییرات بلافاصله به گیت‌هاب ارسال شده و صفحه جدید دریافت می‌شود.</p>
        <p><strong>۲. حالت زنجیره‌ای (Batch Mode):</strong> مناسب برای فرم‌های ورود و وب‌اپلیکیشن‌های تک‌صفحه‌ای (SPA). چندین کلیک و تایپ را پشت سر هم ثبت کنید و با زدن دکمه 🚀 همه را در یک اجرای رانر در گیت‌هاب اجرا کنید.</p>
      </div>

      <div class="card">
        <h3>وضعیت اتصال و نشست‌ها</h3>
        <p><strong>توکن PAT:</strong> <span class="badge badge-ok">{token_status}</span></p>
        <p><strong>مخزن هدف:</strong> <span dir="ltr" style="color:#60a5fa;">{repo_status}</span></p>
        <p><strong>کوکی‌های فعال:</strong> <span class="badge badge-ok">{cookies_count} عدد</span></p>
        <p style="margin-top:10px;"><a style="color:#38bdf8;cursor:pointer;text-decoration:underline;" onclick="parent.window.shellApp.openSettings()">تغییر تنظیمات اتصال</a></p>
      </div>
    </div>
  </div>

  <script>
    function doSearch() {{
      const q = document.getElementById('start-query').value.trim();
      if (!q) return;
      try {{
        if (parent && parent.window && parent.window.shellApp) {{
          parent.window.shellApp.urlBar.value = q;
          parent.window.shellApp.onGoClick();
          return;
        }}
      }} catch (e) {{}}
      if (parent && parent.window && parent.window.pywebview && parent.window.pywebview.api) {{
        parent.window.pywebview.api.navigate(q, 0);
      }}
    }}

    function quickNav(url) {{
      if (!url) return;
      try {{
        if (parent && parent.window && parent.window.shellApp) {{
          parent.window.shellApp.urlBar.value = url;
          parent.window.shellApp.onGoClick();
          return;
        }}
      }} catch (e) {{}}
      if (parent && parent.window && parent.window.pywebview && parent.window.pywebview.api) {{
        parent.window.pywebview.api.navigate(url, 0);
      }}
    }}
  </script>
</body>
</html>
"""


class WebviewApiBridge:
    """Python bridge exposed to JavaScript under window.pywebview.api."""

    def __init__(self, ui: OfflineBrowserUI):
        # Must use leading underscore so pywebview does not inspect ui.window.native recursively
        self._ui = ui

    def set_delay(self, seconds: Any = 0):
        self._ui.set_delay(seconds)

    def get_delay(self) -> int:
        return self._ui.current_delay_seconds

    def navigate(self, target_url: str, custom_delay_seconds: int = 0):
        self._ui.dispatch_navigate(target_url, custom_delay_seconds)

    def submit_form(
        self, action_url: str, method: str, content_type: str, body_base64: str, custom_delay_seconds: int = 0
    ):
        self._ui.dispatch_form_submit(action_url, method, content_type, body_base64, custom_delay_seconds)

    def dispatch_replay(
        self,
        target_url: str,
        actions: List[Dict[str, Any]],
        intercepted_ajax: Optional[Dict[str, Any]] = None,
        custom_delay_seconds: int = 0,
    ):
        self._ui.dispatch_replay(target_url, actions, intercepted_ajax, custom_delay_seconds)

    def run_batch_actions(self, target_url: str, actions: List[Dict[str, Any]], custom_delay_seconds: int = 0):
        self._ui.dispatch_replay(target_url, actions, None, custom_delay_seconds)

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
        return self._ui.config.to_dict()

    def get_daemon_status(self) -> Dict[str, Any]:
        alive = self._ui.transport.is_daemon_alive()
        return {"alive": alive}

    def toggle_daemon(self) -> Dict[str, Any]:
        alive = self._ui.transport.is_daemon_alive()
        if alive:
            ok, msg = self._ui.transport.stop_daemon()
            return {"alive": False, "msg": "رانر زنده متوقف شد."}
        else:
            ok, msg = self._ui.transport.start_daemon(idle_timeout_min=30)
            return {"alive": True, "msg": "درخواست راه‌اندازی رانر زنده ارسال شد. ظرف ۳۰ الی ۴۵ ثانیه آماده می‌شود..."}

    def save_config(
        self, token: str, repo: str, branch: str, search_url: str, encryption_key: str = ""
    ) -> bool:
        try:
            self._ui.config.github_token = token.strip()
            self._ui.config.github_repo = repo.strip()
            self._ui.config.branch = branch.strip() or "main"
            if search_url:
                self._ui.config.default_search_url = search_url.strip()
            self._ui.config.encryption_key = encryption_key.strip()
            self._ui.config.save()
            return True
        except Exception as e:
            logger.error(f"Save config error: {e}")
            return False
