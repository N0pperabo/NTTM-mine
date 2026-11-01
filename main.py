"""
GitHub-Based Covert Channel Offline Browser
Application Entrypoint
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from client.browser_ui import OfflineBrowserUI
from client.config import BrowserConfig
from client.session_manager import SessionManager
from client.transport import GitHubTransport

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("Main")


def main():
    parser = argparse.ArgumentParser(
        description="GitHub-Based Offline Browser & SPA Action Replayer"
    )
    parser.add_argument(
        "--url",
        type=str,
        default="",
        help="آدرس اولیه برای باز شدن در هنگام شروع برنامه",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="اجرای برنامه و باز شدن مستقیم در مرورگر پیش‌فرض سیستم به جای پنجره دسکتاپ پایتون",
    )
    args = parser.parse_args()

    logger.info("Starting GitHub Offline Browser...")

    # 1. Load or initialize configuration
    config = BrowserConfig.load()
    if not config.is_configured():
        logger.warning(
            "GitHub PAT or Repository not configured yet. Please configure via UI Settings."
        )

    # 2. Initialize Session Manager (cookies, localStorage, sessionStorage)
    session_manager = SessionManager()

    # 3. Initialize GitHub Transport
    transport = GitHubTransport(config=config, session_manager=session_manager)

    # 4. Initialize and launch UI Shell
    ui = OfflineBrowserUI(transport=transport, session_manager=session_manager)

    if args.url:
        ui.current_url = args.url

    try:
        ui.launch(browser_mode=args.browser)
    except KeyboardInterrupt:
        logger.info("Application closed by user.")
    except Exception as e:
        logger.exception(f"Fatal UI error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
