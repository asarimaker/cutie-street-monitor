import json
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

import monitor


LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8765
CONNECT_TIMEOUT_SECONDS = 60
COLLECTION_TIMEOUT_SECONDS = 20 * 60


def find_chrome() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Google Chrome was not found.")


class CollectionServer(ThreadingHTTPServer):
    def __init__(self, token: str, previous_ids: set[str]):
        super().__init__((LOCAL_HOST, LOCAL_PORT), CollectionHandler)
        self.token = token
        self.previous_ids = previous_ids
        self.connected = threading.Event()
        self.completed = threading.Event()
        self.result: dict | None = None


class CollectionHandler(BaseHTTPRequestHandler):
    server: CollectionServer

    def log_message(self, format_string: str, *args) -> None:
        return

    def send_json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def valid_token(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        return secrets.compare_digest(query.get("token", [""])[0], self.server.token)

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/session" or not self.valid_token():
            self.send_json(403, {"error": "Invalid local monitor session."})
            return
        self.server.connected.set()
        self.send_json(
            200,
            {
                "username": monitor.USERNAME,
                "previous_ids": sorted(self.server.previous_ids),
                "max_scrolls": monitor.MANUAL_MAX_SCROLLS,
                "scroll_delay_ms": monitor.MANUAL_SCROLL_DELAY_MS,
                "stalled_scroll_limit": monitor.MANUAL_STALLED_SCROLL_LIMIT,
            },
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/result" or not self.valid_token():
            self.send_json(403, {"error": "Invalid local monitor session."})
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > 20_000_000:
            self.send_json(400, {"error": "Invalid result size."})
            return
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid result JSON."})
            return
        if not secrets.compare_digest(str(value.get("token", "")), self.server.token):
            self.send_json(403, {"error": "Invalid result token."})
            return
        self.server.result = value
        self.send_json(200, {"accepted": True})
        self.server.completed.set()


def process_result(result: dict) -> None:
    if result.get("status") != "success":
        raise RuntimeError(result.get("error", "Chrome collection failed."))

    posts = result.get("posts")
    if not isinstance(posts, list) or not posts:
        raise RuntimeError("Chrome returned no timeline posts.")

    now = datetime.now(monitor.JST)
    archive = monitor.read_json(monitor.ARCHIVE_FILE, monitor.default_archive())
    state = monitor.read_json(monitor.STATE_FILE, monitor.default_state())
    previous_ids = set(archive.get("tweets", {}))
    archive_was_empty = not previous_ids
    raw_article_count = int(result.get("raw_article_count", len(posts)))
    coverage_event = monitor.detect_coverage_event(
        archive_was_empty,
        previous_ids,
        posts,
        raw_article_count,
        now,
    )
    new_count, edited_count, new_ids, edited_ids = monitor.merge_posts(
        archive,
        posts,
        now,
    )

    if new_count or edited_count:
        archive["updated_at"] = now.isoformat()
    if coverage_event:
        monitor.append_event(state, coverage_event)
    if state.get("active_issue") is not None:
        state["active_issue"] = None
    if monitor.heartbeat_due(state, now):
        state["last_heartbeat_at"] = now.isoformat()

    monitor.write_json(monitor.ARCHIVE_FILE, archive)
    monitor.write_json(monitor.LATEST_FILE, monitor.build_daily_output(archive, state, now))
    state["last_daily_generated_at"] = now.isoformat()
    state["updated_at"] = now.isoformat()
    monitor.write_json(monitor.STATE_FILE, state)

    print(
        json.dumps(
            {
                "status": "success",
                "collected_posts": len(posts),
                "new_posts": new_count,
                "edited_posts": edited_count,
                "overlap_found": bool(result.get("overlap_found")),
                "new_post_urls": [archive["tweets"][post_id]["url"] for post_id in new_ids],
                "edited_post_urls": [archive["tweets"][post_id]["url"] for post_id in edited_ids],
                "coverage_event": coverage_event,
                "archive_size": len(archive.get("tweets", {})),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    os.chdir(REPOSITORY_ROOT)
    monitor.DATA_DIR.mkdir(exist_ok=True)
    archive = monitor.read_json(monitor.ARCHIVE_FILE, monitor.default_archive())
    previous_ids = set(archive.get("tweets", {}))
    token = secrets.token_urlsafe(32)
    server = CollectionServer(token, previous_ids)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        chrome = find_chrome()
        profile_url = f"{monitor.PROFILE_URL}#cutie-monitor={token}"
        subprocess.Popen(
            [str(chrome), profile_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("Opened the CUTIE STREET profile in your signed-in Chrome session.")
        print("Waiting for the local Chrome extension...")
        if not server.connected.wait(CONNECT_TIMEOUT_SECONDS):
            raise RuntimeError(
                "The Chrome extension did not connect. Confirm that it is installed and enabled."
            )
        print("Collecting posts in Chrome...")
        if not server.completed.wait(COLLECTION_TIMEOUT_SECONDS):
            raise TimeoutError("Chrome collection did not finish within 20 minutes.")
        process_result(server.result or {})
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
