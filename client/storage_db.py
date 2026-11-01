import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("StorageDB")


def get_default_db_path() -> Path:
    """Returns the default SQLite database path in ~/.gh_offline_browser/nttm_data.db."""
    base_dir = Path.home() / ".gh_offline_browser"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "nttm_data.db"


class StorageDB:
    """
    Lightweight, high-performance SQLite storage database for browsing history,
    bookmarks, and page cache metadata. Thread-safe with WAL mode enabled.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        self.db_path = Path(db_path) if db_path else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a thread-local SQLite connection with dictionary-like row factory."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode
            )
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for high concurrency
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        """Creates tables and indexes if they do not exist."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        title TEXT DEFAULT '',
                        timestamp REAL NOT NULL,
                        local_cache_path TEXT DEFAULT '',
                        visit_count INTEGER DEFAULT 1
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp DESC);")

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bookmarks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL UNIQUE,
                        title TEXT DEFAULT '',
                        folder TEXT DEFAULT 'default',
                        created_at REAL NOT NULL
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_folder ON bookmarks(folder);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_url ON bookmarks(url);")

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS page_cache (
                        url TEXT PRIMARY KEY,
                        hash TEXT DEFAULT '',
                        size INTEGER DEFAULT 0,
                        mime_type TEXT DEFAULT 'text/html',
                        cached_at REAL NOT NULL
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_page_cache_cached_at ON page_cache(cached_at DESC);")
        logger.info(f"StorageDB initialized at {self.db_path}")

    # ---------------- History Operations ----------------

    def add_history(self, url: str, title: str = "", local_cache_path: str = "") -> int:
        """
        Records or updates a visited URL in history.
        If URL already exists, increments visit_count and updates timestamp and cache path.
        """
        clean_url = (url or "").strip()
        if not clean_url or clean_url.startswith("about:"):
            return 0

        now = time.time()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, visit_count FROM history WHERE url = ? ORDER BY timestamp DESC LIMIT 1;", (clean_url,))
            row = cursor.fetchone()

            if row:
                hist_id = row["id"]
                cursor.execute(
                    """
                    UPDATE history
                    SET title = CASE WHEN ? != '' THEN ? ELSE title END,
                        timestamp = ?,
                        local_cache_path = CASE WHEN ? != '' THEN ? ELSE local_cache_path END,
                        visit_count = visit_count + 1
                    WHERE id = ?;
                    """,
                    (title, title, now, local_cache_path, local_cache_path, hist_id),
                )
                return hist_id
            else:
                cursor.execute(
                    """
                    INSERT INTO history (url, title, timestamp, local_cache_path, visit_count)
                    VALUES (?, ?, ?, ?, 1);
                    """,
                    (clean_url, title, now, local_cache_path),
                )
                return cursor.lastrowid or 0

    def get_history(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Retrieves history entries ordered by most recent visit."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, url, title, timestamp, local_cache_path, visit_count
                FROM history
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def search_history(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Searches history by matching URL or page title."""
        pattern = f"%{(query or '').strip()}%"
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, url, title, timestamp, local_cache_path, visit_count
                FROM history
                WHERE url LIKE ? OR title LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?;
                """,
                (pattern, pattern, limit),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def clear_history(self) -> bool:
        """Purges all entries from history table."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("DELETE FROM history;")
            return True

    # ---------------- Bookmark Operations ----------------

    def add_bookmark(self, url: str, title: str = "", folder: str = "default") -> bool:
        """Adds or updates a bookmark for the given URL."""
        clean_url = (url or "").strip()
        if not clean_url or clean_url.startswith("about:"):
            return False

        now = time.time()
        folder_clean = (folder or "default").strip()
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO bookmarks (url, title, folder, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE bookmarks.title END,
                    folder = excluded.folder;
                """,
                (clean_url, title, folder_clean, now),
            )
            return True

    def get_bookmarks(self, folder: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves bookmarks, optionally filtered by folder name."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if folder:
                cursor.execute(
                    """
                    SELECT id, url, title, folder, created_at
                    FROM bookmarks
                    WHERE folder = ?
                    ORDER BY created_at DESC;
                    """,
                    (folder.strip(),),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, url, title, folder, created_at
                    FROM bookmarks
                    ORDER BY created_at DESC;
                    """
                )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def remove_bookmark(self, bookmark_id_or_url: Union[int, str]) -> bool:
        """Removes a bookmark by numeric ID or by exact URL."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if isinstance(bookmark_id_or_url, int) or (isinstance(bookmark_id_or_url, str) and bookmark_id_or_url.isdigit()):
                cursor.execute("DELETE FROM bookmarks WHERE id = ?;", (int(bookmark_id_or_url),))
            else:
                cursor.execute("DELETE FROM bookmarks WHERE url = ?;", (str(bookmark_id_or_url).strip(),))
            return cursor.rowcount > 0

    def is_bookmarked(self, url: str) -> bool:
        """Checks if a URL is currently bookmarked."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM bookmarks WHERE url = ? LIMIT 1;", (url.strip(),))
            return cursor.fetchone() is not None

    # ---------------- Page Cache Metadata ----------------

    def set_page_cache(
        self,
        url: str,
        hash: str = "",
        size: int = 0,
        mime_type: str = "text/html",
        cached_at: Optional[float] = None,
    ) -> bool:
        """Upserts metadata for a cached page or resource."""
        clean_url = (url or "").strip()
        if not clean_url:
            return False

        ts = cached_at if cached_at is not None else time.time()
        with self._lock:
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO page_cache (url, hash, size, mime_type, cached_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    hash = excluded.hash,
                    size = excluded.size,
                    mime_type = excluded.mime_type,
                    cached_at = excluded.cached_at;
                """,
                (clean_url, hash or "", int(size or 0), mime_type or "text/html", ts),
            )
            return True

    def get_page_cache(self, url: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached metadata for a URL."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT url, hash, size, mime_type, cached_at FROM page_cache WHERE url = ?;", (url.strip(),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def clear_page_cache(self) -> bool:
        """Purges all entries from page_cache table."""
        with self._lock:
            conn = self._get_connection()
            conn.execute("DELETE FROM page_cache;")
            return True

    # ---------------- Stats & Maintenance ----------------

    def get_stats(self) -> Dict[str, Any]:
        """Returns statistics on stored history, bookmarks, page cache, and DB file size."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) AS cnt FROM history;")
            history_count = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) AS cnt FROM bookmarks;")
            bookmarks_count = cursor.fetchone()["cnt"]

            cursor.execute("SELECT COUNT(*) AS cnt FROM page_cache;")
            cache_count = cursor.fetchone()["cnt"]

            db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

            return {
                "db_path": str(self.db_path),
                "history_count": history_count,
                "bookmarks_count": bookmarks_count,
                "page_cache_count": cache_count,
                "db_size_bytes": db_size_bytes,
                "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
            }

    def close(self) -> None:
        """Closes thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None


# Global singleton instance
_GLOBAL_DB: Optional[StorageDB] = None
_GLOBAL_DB_LOCK = threading.Lock()


def get_storage_db(db_path: Optional[Union[str, Path]] = None) -> StorageDB:
    """Returns or initializes the global StorageDB instance."""
    global _GLOBAL_DB
    with _GLOBAL_DB_LOCK:
        if _GLOBAL_DB is None or (db_path and Path(db_path) != _GLOBAL_DB.db_path):
            _GLOBAL_DB = StorageDB(db_path)
        return _GLOBAL_DB


# ---------------- Module-Level Convenience Functions ----------------

def add_history(url: str, title: str = "", local_cache_path: str = "") -> int:
    return get_storage_db().add_history(url, title, local_cache_path)


def get_history(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    return get_storage_db().get_history(limit, offset)


def clear_history() -> bool:
    return get_storage_db().clear_history()


def add_bookmark(url: str, title: str = "", folder: str = "default") -> bool:
    return get_storage_db().add_bookmark(url, title, folder)


def get_bookmarks(folder: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_storage_db().get_bookmarks(folder)


def remove_bookmark(bookmark_id_or_url: Union[int, str]) -> bool:
    return get_storage_db().remove_bookmark(bookmark_id_or_url)


def search_history(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    return get_storage_db().search_history(query, limit)


def is_bookmarked(url: str) -> bool:
    return get_storage_db().is_bookmarked(url)


def set_page_cache(url: str, hash: str = "", size: int = 0, mime_type: str = "text/html", cached_at: Optional[float] = None) -> bool:
    return get_storage_db().set_page_cache(url, hash, size, mime_type, cached_at)


def get_page_cache(url: str) -> Optional[Dict[str, Any]]:
    return get_storage_db().get_page_cache(url)


def clear_page_cache() -> bool:
    return get_storage_db().clear_page_cache()
