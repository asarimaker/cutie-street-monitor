import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from playwright.sync_api import Locator


USERNAME = "CUTIE_STREET_"
PROFILE_URL = f"https://x.com/{USERNAME}"
DATA_DIR = Path("data")
ARCHIVE_FILE = DATA_DIR / "archive.json"
STATE_FILE = DATA_DIR / "monitor_state.json"
LATEST_FILE = DATA_DIR / "latest.json"
DIAGNOSTICS_DIR = Path("diagnostics")
JST = ZoneInfo("Asia/Tokyo")
HEARTBEAT_HOURS = 6
EXPECTED_PUBLIC_ITEMS = 5
MANUAL_MAX_SCROLLS = 250
MANUAL_SCROLL_DELAY_MS = 1_500
MANUAL_STALLED_SCROLL_LIMIT = 8
MANUAL_LOGIN_TIMEOUT_MS = 15 * 60 * 1_000
LOGGED_IN_SELECTOR = (
    '[data-testid="SideNav_AccountSwitcher_Button"], '
    'a[data-testid="AppTabBar_Home_Link"]'
)
X_SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
STATUS_RE = re.compile(r"/([^/]+)/status/(\d+)", re.IGNORECASE)


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def save_page_diagnostics(page, http_status: int | None, error: Exception) -> None:
    """Save enough of a failed X response to diagnose access and DOM failures."""
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    capture_errors: list[str] = []

    def capture(label: str, getter, default):
        try:
            return getter()
        except Exception as exc:
            capture_errors.append(f"{label}: {type(exc).__name__}: {exc}")
            return default

    body_text = capture(
        "body_text",
        lambda: page.locator("body").inner_text(timeout=5_000),
        "",
    )
    html = capture("html", page.content, "")
    diagnostics = {
        "captured_at": datetime.now(JST).isoformat(),
        "profile_url": PROFILE_URL,
        "final_url": capture("final_url", lambda: page.url, ""),
        "title": capture("title", page.title, ""),
        "http_status": http_status,
        "article_count": capture(
            "article_count",
            lambda: page.locator("article").count(),
            None,
        ),
        "error": f"{type(error).__name__}: {error}",
        "body_text": body_text[:10_000],
        "capture_errors": capture_errors,
    }

    write_json(DIAGNOSTICS_DIR / "page-diagnostics.json", diagnostics)
    (DIAGNOSTICS_DIR / "page.html").write_text(html, encoding="utf-8")
    try:
        page.screenshot(
            path=str(DIAGNOSTICS_DIR / "page-screenshot.png"),
            full_page=True,
        )
    except Exception as exc:
        diagnostics["capture_errors"].append(
            f"screenshot: {type(exc).__name__}: {exc}"
        )
        write_json(DIAGNOSTICS_DIR / "page-diagnostics.json", diagnostics)


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(JST)


def completed_report_window(now: datetime) -> tuple[datetime, datetime]:
    today_at_seven = now.replace(hour=7, minute=0, second=0, microsecond=0)
    end = today_at_seven if now >= today_at_seven else today_at_seven - timedelta(days=1)
    return end - timedelta(hours=24), end


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def snowflake_datetime(post_id: str) -> datetime:
    timestamp_ms = (int(post_id) >> 22) + X_SNOWFLAKE_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1_000, tz=ZoneInfo("UTC")).astimezone(JST)


def fallback_article_text(body: str) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    handle_index = next(
        (index for index, line in enumerate(lines) if line.casefold() == f"@{USERNAME}".casefold()),
        None,
    )
    if handle_index is None or handle_index + 2 >= len(lines):
        return ""

    content = lines[handle_index + 2 :]

    # Stop before an embedded quoted post begins.
    for index in range(len(content) - 1):
        if (
            content[index] == "CUTIE STREET【Official】"
            and content[index + 1].casefold() == f"@{USERNAME}".casefold()
        ):
            content = content[:index]
            break

    metric_pattern = re.compile(
        r"^(?:[\d.,]+(?:万|億|K|M)?|\d{1,2}:\d{2})$",
        re.IGNORECASE,
    )
    while content and metric_pattern.fullmatch(content[-1]):
        content.pop()

    text = "\n".join(content).strip()
    return re.sub(r"\s*さらに表示\s*$", "", text).strip()


def is_daily_mode() -> bool:
    return os.environ.get("DAILY_MODE", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
    }


def is_manual_mode() -> bool:
    return os.environ.get("MANUAL_MODE", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
    }


def local_browser_profile_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.cwd())))
    return base / "cutie-street-monitor" / "browser-profile"


def extract_article(article: "Locator") -> dict | None:
    try:
        body = article.inner_text(timeout=5_000)
        links = article.locator('a[href*="/status/"]')
        candidates: list[tuple[str, str, str]] = []
        seen_candidates: set[tuple[str, str]] = set()

        # Logged-out X omits <time datetime>, but the Snowflake post ID itself
        # encodes the exact creation timestamp. Preserve DOM order so an outer
        # quote post is selected before the embedded quoted post.
        for index in range(links.count()):
            href = links.nth(index).get_attribute("href")
            match = STATUS_RE.search(href or "")
            if not match:
                continue
            key = (match.group(1), match.group(2))
            if key not in seen_candidates:
                candidates.append((href or "", match.group(1), match.group(2)))
                seen_candidates.add(key)

        if not candidates:
            return None

        first_lines = "\n".join(body.splitlines()[:5])
        own_candidates = [
            item for item in candidates if item[1].casefold() == USERNAME.casefold()
        ]
        reposted = bool(
            re.search(
                r"リポストしました|さんがリポスト|\breposted\b",
                first_lines,
                re.IGNORECASE,
            )
        )

        # Quoted posts may contain a nested <article>. Count only posts authored or
        # explicitly reposted by the monitored profile.
        if own_candidates:
            _, author, post_id = own_candidates[0]
        elif reposted:
            _, author, post_id = candidates[0]
        else:
            return None

        created_at = snowflake_datetime(post_id)
        status_ids = unique([item[2] for item in candidates])
        content_nodes = article.locator('[data-testid="tweetText"], div[lang]')
        text = (
            content_nodes.first.inner_text(timeout=5_000)
            if content_nodes.count()
            else fallback_article_text(body)
        )
        image_urls = article.locator('img[src*="pbs.twimg.com/media/"]').evaluate_all(
            "elements => elements.map(element => element.src)"
        )
        video_nodes = article.locator(
            'video, [data-testid="videoPlayer"], [data-testid="videoComponent"]'
        )
        video_urls = article.locator("video").evaluate_all(
            "elements => elements.map(element => element.currentSrc || element.src || '')"
        )
        replying = bool(re.search(r"返信先:|Replying to", body, re.IGNORECASE))

        if reposted:
            post_type = "repost"
        elif replying:
            post_type = "reply"
        elif len(status_ids) > 1:
            post_type = "quote"
        else:
            post_type = "post"

        pinned = bool(re.search(r"(^|\n)(固定|Pinned)(\n|$)", first_lines, re.IGNORECASE))

        return {
            "id": post_id,
            "author": author,
            "created_at": created_at.isoformat(),
            "text": text,
            "type": post_type,
            "pinned": pinned,
            "images": unique(image_urls),
            "has_video": video_nodes.count() > 0,
            "videos": unique([url for url in video_urls if url.startswith("http")]),
            "url": f"https://x.com/{author}/status/{post_id}",
        }
    except Exception as exc:
        print(f"Skipping an unparseable article: {type(exc).__name__}: {exc}")
        return None


def collect_page_posts(page, posts: dict[str, dict]) -> tuple[int, int]:
    articles = page.locator("article")
    raw_article_count = articles.count()
    top_level_article_count = 0
    for index in range(raw_article_count):
        article = articles.nth(index)
        if article.locator("xpath=ancestor::article").count():
            continue
        top_level_article_count += 1
        item = extract_article(article)
        if item:
            posts[item["id"]] = item
    return raw_article_count, top_level_article_count


def fetch_visible_posts(previous_ids: set[str] | None = None) -> tuple[list[dict], int]:
    from playwright.sync_api import sync_playwright

    previous_ids = previous_ids or set()
    manual_mode = is_manual_mode()
    with sync_playwright() as playwright:
        browser = None
        context_options = {
            "locale": "ja-JP",
            "timezone_id": "Asia/Tokyo",
            "viewport": {"width": 1440, "height": 1400},
        }
        if not manual_mode:
            context_options["user_agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            )

        if manual_mode:
            profile_dir = local_browser_profile_dir()
            profile_dir.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                **context_options,
            )
        else:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(**context_options)

        try:
            page = context.pages[0] if context.pages else context.new_page()
            response = None
            try:
                if manual_mode:
                    page.goto(
                        "https://x.com/home",
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                    try:
                        page.wait_for_selector(LOGGED_IN_SELECTOR, timeout=15_000)
                    except Exception:
                        page.goto(
                            "https://x.com/i/flow/login",
                            wait_until="domcontentloaded",
                            timeout=60_000,
                        )
                        print(
                            "ブラウザーでXにログインしてください。"
                            "ログイン完了を自動で検出するまでブラウザーを閉じないでください。"
                        )
                        page.wait_for_selector(
                            LOGGED_IN_SELECTOR,
                            timeout=MANUAL_LOGIN_TIMEOUT_MS,
                        )
                        print("Xへのログインを確認しました。投稿を取得します。")

                response = page.goto(
                    PROFILE_URL,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                try:
                    page.wait_for_selector("article", timeout=30_000)
                except Exception:
                    if not manual_mode:
                        raise
                    print(
                        "投稿を表示できません。ブラウザー上で再読み込みまたは"
                        "ログイン状態の確認を行ってください。最大15分待機します。"
                    )
                    page.wait_for_selector(
                        "article",
                        timeout=MANUAL_LOGIN_TIMEOUT_MS,
                    )
                page.wait_for_timeout(5_000)

                posts: dict[str, dict] = {}
                max_raw_article_count = 0
                stalled_scrolls = 0
                overlap_found = False

                scroll_limit = MANUAL_MAX_SCROLLS if manual_mode else 0
                for scroll_index in range(scroll_limit + 1):
                    count_before = len(posts)
                    raw_count, _ = collect_page_posts(page, posts)
                    max_raw_article_count = max(max_raw_article_count, raw_count)

                    non_pinned_ids = {
                        post_id
                        for post_id, item in posts.items()
                        if not item.get("pinned")
                    }
                    overlap = non_pinned_ids & previous_ids
                    if manual_mode and overlap:
                        overlap_found = True
                        print(
                            f"保存済み投稿との重複を確認しました。"
                            f"{scroll_index}回スクロール、{len(posts)}件取得。"
                        )
                        break

                    if scroll_index >= scroll_limit:
                        break

                    if len(posts) == count_before:
                        stalled_scrolls += 1
                    else:
                        stalled_scrolls = 0

                    if stalled_scrolls >= MANUAL_STALLED_SCROLL_LIMIT:
                        print(
                            "これ以上投稿を読み込めなかったため、スクロールを終了します。"
                        )
                        break

                    page.evaluate(
                        "window.scrollBy(0, Math.max(window.innerHeight * 0.85, 800))"
                    )
                    page.wait_for_timeout(MANUAL_SCROLL_DELAY_MS)

                if manual_mode and previous_ids and not overlap_found:
                    print(
                        "警告: 保存済み投稿との重複地点まで到達できませんでした。"
                        "取得漏れの可能性を記録します。"
                    )

                result = sorted(
                    posts.values(),
                    key=lambda item: item["created_at"],
                    reverse=True,
                )
                if not result:
                    article_diagnostics = []
                    articles = page.locator("article")
                    for index in range(min(articles.count(), 8)):
                        article = articles.nth(index)
                        article_diagnostics.append(
                            {
                                "text": article.inner_text(timeout=5_000)[:500],
                                "status_hrefs": article.locator(
                                    'a[href*="/status/"]'
                                ).evaluate_all(
                                    "elements => elements.map(element => element.getAttribute('href'))"
                                ),
                                "times": article.locator("time").evaluate_all(
                                    "elements => elements.map(element => element.getAttribute('datetime'))"
                                ),
                            }
                        )
                    diagnostics = {
                        "final_url": page.url,
                        "title": page.title(),
                        "raw_article_count": max_raw_article_count,
                        "articles": article_diagnostics,
                        "body_start": page.locator("body").inner_text()[:1_000],
                    }
                    raise RuntimeError(
                        "No timeline posts could be parsed. DOM diagnostics: "
                        + json.dumps(diagnostics, ensure_ascii=False)
                    )
                return result, max_raw_article_count
            except Exception as exc:
                save_page_diagnostics(
                    page,
                    response.status if response is not None else None,
                    exc,
                )
                raise
        finally:
            if browser is not None:
                browser.close()
            else:
                context.close()


def default_archive() -> dict:
    return {
        "schema_version": 1,
        "account": USERNAME,
        "updated_at": None,
        "tweets": {},
    }


def default_state() -> dict:
    return {
        "schema_version": 1,
        "account": USERNAME,
        "updated_at": None,
        "last_heartbeat_at": None,
        "last_daily_generated_at": None,
        "active_issue": None,
        "coverage_events": [],
    }


def append_event(state: dict, event: dict) -> None:
    state.setdefault("coverage_events", []).append(event)


def prune_old_data(archive: dict, state: dict, now: datetime) -> bool:
    # Posts and coverage events are retained indefinitely.
    return False


def heartbeat_due(state: dict, now: datetime) -> bool:
    last_value = state.get("last_heartbeat_at")
    if not last_value:
        return True
    return now - parse_datetime(last_value) >= timedelta(hours=HEARTBEAT_HOURS)


def merge_posts(
    archive: dict,
    posts: list[dict],
    now: datetime,
) -> tuple[int, int, list[str], list[str]]:
    new_count = 0
    changed_count = 0
    new_ids: list[str] = []
    changed_ids: list[str] = []
    stored = archive.setdefault("tweets", {})

    for item in posts:
        post_id = item["id"]
        existing = stored.get(post_id)
        if existing is None:
            stored[post_id] = {**item, "first_seen_at": now.isoformat()}
            new_count += 1
            new_ids.append(post_id)
            continue

        comparable_existing = {
            key: value
            for key, value in existing.items()
            if key not in {"first_seen_at", "last_changed_at"}
        }
        if comparable_existing != item:
            stored[post_id] = {
                **item,
                "first_seen_at": existing.get("first_seen_at", now.isoformat()),
                "last_changed_at": now.isoformat(),
            }
            changed_count += 1
            changed_ids.append(post_id)

    return new_count, changed_count, new_ids, changed_ids


def detect_coverage_event(
    archive_was_empty: bool,
    previous_ids: set[str],
    visible_posts: list[dict],
    raw_article_count: int,
    now: datetime,
) -> dict | None:
    non_pinned = [item for item in visible_posts if not item["pinned"]]
    visible_ids = {item["id"] for item in non_pinned}
    overlap = sorted(visible_ids & previous_ids)

    if archive_was_empty:
        return {
            "type": "initial_seed",
            "detected_at": now.isoformat(),
            "message": "Posts older than the initial public-profile view may be missing.",
        }

    if not non_pinned:
        return {
            "type": "no_non_pinned_posts",
            "detected_at": now.isoformat(),
            "message": "No non-pinned timeline posts were available for overlap checking.",
        }

    if not overlap:
        return {
            "type": "possible_gap",
            "detected_at": now.isoformat(),
            "visible_non_pinned_count": len(non_pinned),
            "raw_article_count": raw_article_count,
            "public_item_limit_assumption": EXPECTED_PUBLIC_ITEMS,
            "message": (
                "The visible posts had no overlap with the archive; more than the "
                "publicly visible number of posts may have appeared since the prior snapshot."
            ),
        }

    return None


def build_daily_output(
    archive: dict,
    state: dict,
    now: datetime,
    current_fetch_error: str | None = None,
) -> dict:
    window_start, window_end = completed_report_window(now)
    tweets = [
        item
        for item in archive.get("tweets", {}).values()
        if window_start <= parse_datetime(item["created_at"]) < window_end
    ]
    tweets.sort(key=lambda item: item["created_at"])

    relevant_events = [
        event
        for event in state.get("coverage_events", [])
        if window_start <= parse_datetime(event["detected_at"]) <= now
    ]
    active_issue = state.get("active_issue")
    warnings = list(relevant_events)
    if active_issue and active_issue not in warnings:
        warnings.append(active_issue)

    if current_fetch_error and not archive.get("tweets"):
        status = "error"
    elif warnings or current_fetch_error:
        status = "partial"
    else:
        status = "success"

    return {
        "schema_version": 1,
        "account": USERNAME,
        "status": status,
        "generated_at": now.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "source": "rolling_playwright_public_profile_snapshots",
        "count": len(tweets),
        "warnings": warnings,
        "tweets": tweets,
    }


def record_fetch_error(state: dict, now: datetime, message: str) -> bool:
    active = state.get("active_issue")
    if active and active.get("type") == "fetch_error" and active.get("message") == message:
        return False

    issue = {
        "type": "fetch_error",
        "detected_at": now.isoformat(),
        "message": message,
    }
    state["active_issue"] = issue
    append_event(state, issue)
    return True


def main() -> None:
    now = datetime.now(JST)
    daily_mode = is_daily_mode()
    DATA_DIR.mkdir(exist_ok=True)
    archive = read_json(ARCHIVE_FILE, default_archive())
    state = read_json(STATE_FILE, default_state())
    archive_changed = not ARCHIVE_FILE.exists()
    state_changed = not STATE_FILE.exists()

    try:
        previous_ids = set(archive.get("tweets", {}))
        visible_posts, raw_article_count = fetch_visible_posts(previous_ids)
        archive_was_empty = not previous_ids
        coverage_event = detect_coverage_event(
            archive_was_empty,
            previous_ids,
            visible_posts,
            raw_article_count,
            now,
        )
        new_count, edited_count, new_ids, edited_ids = merge_posts(
            archive,
            visible_posts,
            now,
        )

        if new_count or edited_count:
            archive_changed = True
            archive["updated_at"] = now.isoformat()

        if coverage_event:
            append_event(state, coverage_event)
            state_changed = True

        if state.get("active_issue") is not None:
            state["active_issue"] = None
            state_changed = True

        if heartbeat_due(state, now):
            state["last_heartbeat_at"] = now.isoformat()
            state_changed = True

        if prune_old_data(archive, state, now):
            archive_changed = True
            state_changed = True
            archive["updated_at"] = now.isoformat()

        if daily_mode:
            write_json(LATEST_FILE, build_daily_output(archive, state, now))
            state["last_daily_generated_at"] = now.isoformat()
            state_changed = True

        if archive_changed:
            write_json(ARCHIVE_FILE, archive)
        if state_changed:
            state["updated_at"] = now.isoformat()
            write_json(STATE_FILE, state)

        print(
            json.dumps(
                {
                    "status": "success",
                    "daily_mode": daily_mode,
                    "visible_posts": len(visible_posts),
                    "raw_articles": raw_article_count,
                    "new_posts": new_count,
                    "edited_posts": edited_count,
                    "new_post_urls": [
                        archive["tweets"][post_id]["url"] for post_id in new_ids
                    ],
                    "edited_post_urls": [
                        archive["tweets"][post_id]["url"] for post_id in edited_ids
                    ],
                    "coverage_event": coverage_event,
                    "archive_size": len(archive.get("tweets", {})),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if record_fetch_error(state, now, message):
            state_changed = True
        if heartbeat_due(state, now):
            state["last_heartbeat_at"] = now.isoformat()
            state_changed = True

        if daily_mode:
            write_json(
                LATEST_FILE,
                build_daily_output(archive, state, now, current_fetch_error=message),
            )
            state["last_daily_generated_at"] = now.isoformat()
            state_changed = True

        if archive_changed:
            write_json(ARCHIVE_FILE, archive)
        if state_changed:
            state["updated_at"] = now.isoformat()
            write_json(STATE_FILE, state)

        print(message, file=sys.stderr)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
