import json
import logging
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SessionManager")


class SessionManager:
    """
    Manages persistent local state for Cookies, localStorage, and sessionStorage.
    Stores data at ~/.gh_offline_browser/session_state.json adhering to Playwright's
    storageState schema.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            home_dir = Path.home()
            app_dir = home_dir / ".gh_offline_browser"
            app_dir.mkdir(parents=True, exist_ok=True)
            self.storage_path = app_dir / "session_state.json"
        else:
            self.storage_path = Path(storage_path)
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        self.cookies: List[Dict[str, Any]] = []
        self.origins: List[Dict[str, Any]] = []
        self.session_storage: Dict[str, Dict[str, Any]] = {}
        self.load_session()

    def load_session(self) -> None:
        """Loads session data from disk if file exists."""
        if not self.storage_path.exists():
            self.cookies = []
            self.origins = []
            self.session_storage = {}
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.cookies = data.get("cookies", [])
                self.origins = data.get("origins", [])
                self.session_storage = data.get("session_storage", {})
            logger.info(
                f"Loaded session with {len(self.cookies)} cookies, "
                f"{len(self.origins)} origins, and {len(self.session_storage)} session_storage origins."
            )
        except Exception as e:
            logger.warning(f"Failed to read session file: {e}. Initializing empty session.")
            self.cookies = []
            self.origins = []
            self.session_storage = {}

    def save_session(self) -> None:
        """Persists current session state to disk."""
        data = {
            "cookies": self.cookies,
            "origins": self.origins,
            "session_storage": self.session_storage,
        }
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved session state to {self.storage_path}")
        except Exception as e:
            logger.error(f"Failed to save session state: {e}")

    def get_envelope_state(
        self, target_url: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        """
        Returns (storage_state, session_storage) ready to be embedded into
        requests/<request_id>.json envelope.
        Filters origins (localStorage) and session_storage to only include relevant
        domains for target_url, and caps/drops oversized cache entries (>16KB)
        to prevent upload timeouts over throttled networks.
        """
        MAX_ITEM_LEN = 16384  # 16 KB max per localStorage item

        target_domain = ""
        if target_url:
            try:
                parsed = urllib.parse.urlparse(target_url)
                target_domain = (parsed.netloc or parsed.path).split(":")[0].lower().lstrip("www.")
            except Exception:
                target_domain = ""

        filtered_origins = []
        for o in self.origins:
            orig = o.get("origin", "")
            # Check domain match if target_domain is known
            if target_domain:
                try:
                    orig_host = urllib.parse.urlparse(orig).netloc.split(":")[0].lower().lstrip("www.")
                    if orig_host and (target_domain not in orig_host and orig_host not in target_domain):
                        continue
                except Exception:
                    pass

            # Filter out oversized items (like MediaWikiModuleStore)
            valid_items = [
                item for item in o.get("localStorage", [])
                if len(str(item.get("value", ""))) <= MAX_ITEM_LEN
            ]
            if valid_items:
                filtered_origins.append({"origin": orig, "localStorage": valid_items})

        # Filter session_storage
        filtered_session_storage = {}
        for orig, items in self.session_storage.items():
            if target_domain:
                try:
                    orig_host = urllib.parse.urlparse(orig).netloc.split(":")[0].lower().lstrip("www.")
                    if orig_host and (target_domain not in orig_host and orig_host not in target_domain):
                        continue
                except Exception:
                    pass
            filtered_session_storage[orig] = items

        storage_state = {
            "cookies": self.cookies,
            "origins": filtered_origins,
        }
        return storage_state, filtered_session_storage

    def update_from_remote(self, storage_state_input: Any) -> None:
        """
        Merges remote storage_state (exported by Playwright runner) into local store.
        `storage_state_input` can be a Path/str to a storage_state.json file or a dict.
        """
        if isinstance(storage_state_input, (str, Path)):
            p = Path(storage_state_input)
            if not p.exists():
                logger.warning(f"Remote storage state file not found: {p}")
                return
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"Error loading remote storage state from {p}: {e}")
                return
        elif isinstance(storage_state_input, dict):
            data = storage_state_input
        else:
            logger.warning(f"Unsupported storage state input type: {type(storage_state_input)}")
            return

        # 1. Merge cookies (key: name, domain, path)
        remote_cookies = data.get("cookies", [])
        cookie_map = {
            (c.get("name"), c.get("domain"), c.get("path", "/")): c for c in self.cookies
        }
        for rc in remote_cookies:
            key = (rc.get("name"), rc.get("domain"), rc.get("path", "/"))
            cookie_map[key] = rc
        self.cookies = list(cookie_map.values())

        # 2. Merge origins (localStorage) - skip oversized cache/module blobs
        MAX_ITEM_LEN = 16384
        remote_origins = data.get("origins", [])
        origins_map = {o.get("origin"): o.get("localStorage", []) for o in self.origins}
        for ro in remote_origins:
            orig = ro.get("origin")
            if not orig:
                continue
            curr_items = origins_map.get(orig, [])
            item_map = {
                item.get("name"): item.get("value")
                for item in curr_items
                if len(str(item.get("value", ""))) <= MAX_ITEM_LEN
            }
            for new_item in ro.get("localStorage", []):
                val_str = str(new_item.get("value", ""))
                if len(val_str) <= MAX_ITEM_LEN:
                    item_map[new_item.get("name")] = new_item.get("value")
            origins_map[orig] = [
                {"name": k, "value": v} for k, v in item_map.items()
            ]

        self.origins = [{"origin": k, "localStorage": v} for k, v in origins_map.items() if v]

        self.save_session()
        logger.info(
            f"Updated session state: now {len(self.cookies)} cookies, {len(self.origins)} origins."
        )

    def merge_client_delta(
        self,
        origin: str,
        local_storage_dict: Optional[Dict[str, str]] = None,
        session_storage_dict: Optional[Dict[str, str]] = None,
        cookie_str: Optional[str] = None,
    ) -> None:
        """
        Merges client-side updates (e.g. from pywebview execution context).
        """
        if not origin:
            return

        # Update localStorage for this origin
        if local_storage_dict:
            found = False
            for orig_entry in self.origins:
                if orig_entry.get("origin") == origin:
                    curr_items = {
                        item["name"]: item["value"]
                        for item in orig_entry.get("localStorage", [])
                    }
                    curr_items.update(local_storage_dict)
                    orig_entry["localStorage"] = [
                        {"name": k, "value": v} for k, v in curr_items.items()
                    ]
                    found = True
                    break
            if not found:
                self.origins.append(
                    {
                        "origin": origin,
                        "localStorage": [
                            {"name": k, "value": v}
                            for k, v in local_storage_dict.items()
                        ],
                    }
                )

        # Update sessionStorage for this origin
        if session_storage_dict:
            if origin not in self.session_storage:
                self.session_storage[origin] = {}
            self.session_storage[origin].update(session_storage_dict)

        # Update cookies if cookie_str provided ("k1=v1; k2=v2")
        if cookie_str:
            import urllib.parse
            parsed_origin = urllib.parse.urlparse(origin)
            domain = parsed_origin.hostname or "localhost"
            cookie_pairs = [c.strip() for c in cookie_str.split(";") if c.strip()]
            for pair in cookie_pairs:
                if "=" in pair:
                    name, val = pair.split("=", 1)
                    name = name.strip()
                    val = val.strip()
                    # Update or append cookie
                    found_c = False
                    for c in self.cookies:
                        if c.get("name") == name and c.get("domain") == domain:
                            c["value"] = val
                            found_c = True
                            break
                    if not found_c:
                        self.cookies.append(
                            {
                                "name": name,
                                "value": val,
                                "domain": domain,
                                "path": "/",
                                "expires": -1,
                                "httpOnly": False,
                                "secure": False,
                                "sameSite": "Lax",
                            }
                        )

        self.save_session()

    def import_netscape_cookies(self, content_or_path: Any) -> int:
        """
        Parses Netscape / curl cookie format text and merges into cookies store.
        Returns count of imported cookies.
        """
        raw_text = ""
        if isinstance(content_or_path, (str, Path)) and Path(str(content_or_path)).exists():
            try:
                raw_text = Path(str(content_or_path)).read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning(f"Failed to read cookie file {content_or_path}: {e}")
                return 0
        elif isinstance(content_or_path, str):
            raw_text = content_or_path
        else:
            return 0

        new_cookies = []
        for line in raw_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain = parts[0]
                path = parts[2]
                secure = parts[3].upper() == "TRUE"
                try:
                    exp = float(parts[4])
                    expiration = exp if exp > 0 else -1
                except Exception:
                    expiration = -1
                name = parts[5]
                val = parts[6]
                new_cookies.append(
                    {
                        "name": name,
                        "value": val,
                        "domain": domain,
                        "path": path,
                        "expires": expiration,
                        "httpOnly": False,
                        "secure": secure,
                        "sameSite": "Lax",
                    }
                )

        if not new_cookies:
            return 0

        cookie_map = {
            (c.get("name"), c.get("domain"), c.get("path", "/")): c for c in self.cookies
        }
        for c in new_cookies:
            key = (c.get("name"), c.get("domain"), c.get("path", "/"))
            cookie_map[key] = c

        self.cookies = list(cookie_map.values())
        self.save_session()
        logger.info(f"Imported {len(new_cookies)} Netscape cookies into session.")
        return len(new_cookies)

    def clear_session(self) -> None:
        """Clears all session cookies, origins, and sessionStorage."""
        self.cookies = []
        self.origins = []
        self.session_storage = {}
        self.save_session()
        logger.info("Session state cleared completely.")
