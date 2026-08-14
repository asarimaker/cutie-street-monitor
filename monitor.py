import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Locator, sync_playwright


USERNAME = "CUTIE_STREET_"
PROFILE_URL = f"https://x.com/{USERNAME}"
DATA_DIR = Path("data")
ARCHIVE_FILE = DATA_DIR / "archive.json"
STATE_FILE = DATA_DIR / "monitor_state.json"
LATEST_FILE = DATA_DIR / "latest.json"
JST = ZoneInfo("Asia/Tokyo")
ARCHIVE_DAYS = 35
HEARTBEAT_HOURS = 6
EXPECTED_PUBLIC_ITEMS = 5
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


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(JST)


def completed_report_window(now: datetime) -> tuple[datetime, datetime]:
    today_at_seven = now.replace(hour=7, minute=0, second=0, microsecond=0)
    end = today_at_seven if now >= today_at_seven else today_at_seven - timedelta(days=1)
    return end - timedelta(hours=24), end


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def is_daily_mode() -> bool:
    return os.environ.get("DAILY_MODE", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
    }


def extract_article(article: Locator) -> dict | None:
    try:
        body = article.inner_text(timeout=5_000)
        links = article.locator('a[href*="/status/"]')
        times = article.locator("time[datetime]")
        candidates: list[tuple[str, str, str, str]] = []

        # X normally wraps each <time> in its permalink. Starting from the time
        # element is more stable than assuming every status link contains time.
        for index in range(times.count()):
            time = times.nth(index)
            timestamp = time.get_attribute("datetime")
            parent_link = time.locator("xpath=ancestor::a[contains(@href, '/status/')][1]")
            href = parent_link.get_attribute("href") if parent_link.count() else None
            match = STATUS_RE.search(href or "")
            if match and timestamp:
                candidates.append((href or "", timestamp, match.group(1), match.group(2)))

        # Fallback for a DOM variant where <time> and the permalink are siblings.
        if not candidates and times.count() and links.count():
            timestamp = times.first.get_attribute("datetime")
            for index in range(links.count()):
                href = links.nth(index).get_attribute("href")
                match = STATUS_RE.search(href or "")
                if match and timestamp:
                    candidates.append((href or "", timestamp, match.group(1), match.group(2)))

        if not candidates:
            return None

        first_lines = "\n".join(body.splitlines()[:5])
        own_candidates = [
            item for item in candidates if item[2].casefold() == USERNAME.casefold()
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
            _, timestamp, author, post_id = own_candidates[0]
        elif reposted:
            _, timestamp, author, post_id = candidates[0]
        else:
            return None

        created_at = parse_datetime(timestamp)
        tweet_texts = article.locator('[data-testid="tweetText"]')
        text = tweet_texts.first.inner_text(timeout=5_000) if tweet_texts.count() else ""
        image_urls = article.locator('img[src*="pbs.twimg.com/media/"]').evaluate_all(
            "elements => elements.map(element => element.src)"
        )
        video_nodes = article.locator(
            'video, [data-testid="videoPlayer"], [data-testid="videoComponent"]'
        )
        video_urls = article.locator("video").evaluate_all(
            "elements => elements.map(element => element.currentSrc || element.src || '')"
        )
        status_ids = unique([item[3] for item in candidates])
        replying = bool(re.search(r"返信先:|Replying to", body, re.IGNORECASE))

        if reposted:
            post_type = "repost"
        elif replying:
            post_type = "reply"
        elif len(status_ids) > 1 and tweet_texts.count() > 1:
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


def fetch_visible_posts() -> tuple[list[dict], int]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                viewport={"width": 1440, "height": 1400},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/151.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("article", timeout=30_000)
            page.wait_for_timeout(5_000)

            articles = page.locator("article")
            raw_article_count = articles.count()
            posts: dict[str, dict] = {}
            for index in range(raw_article_count):
                item = extract_article(articles.nth(index))
                if item:
                    posts[item["id"]] = item

            result = sorted(posts.values(), key=lambda item: item["created_at"], reverse=True)
            if not result:
                article_diagnostics = []
                for index in range(min(raw_article_count, 8)):
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
                    "raw_article_count": raw_article_count,
                    "articles": article_diagnostics,
                    "body_start": page.locator("body").inner_text()[:1_000],
                }
                raise RuntimeError(
                    "No timeline posts could be parsed. DOM diagnostics: "
                    + json.dumps(diagnostics, ensure_ascii=False)
                )
            return result, raw_article_count
        finally:
            browser.close()


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
    cutoff = now - timedelta(days=ARCHIVE_DAYS)
    changed = False

    for post_id, item in list(archive.get("tweets", {}).items()):
        if parse_datetime(item["created_at"]) < cutoff:
            del archive["tweets"][post_id]
            changed = True

    old_events = state.get("coverage_events", [])
    new_events = [
        event
        for event in old_events
        if parse_datetime(event["detected_at"]) >= cutoff
    ]
    if new_events != old_events:
        state["coverage_events"] = new_events
        changed = True

    return changed


def heartbeat_due(state: dict, now: datetime) -> bool:
    last_value = state.get("last_heartbeat_at")
    if not last_value:
        return True
    return now - parse_datetime(last_value) >= timedelta(hours=HEARTBEAT_HOURS)


def merge_posts(archive: dict, posts: list[dict], now: datetime) -> tuple[int, int]:
    new_count = 0
    changed_count = 0
    stored = archive.setdefault("tweets", {})

    for item in posts:
        post_id = item["id"]
        existing = stored.get(post_id)
        if existing is None:
            stored[post_id] = {**item, "first_seen_at": now.isoformat()}
            new_count += 1
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

    return new_count, changed_count


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
        visible_posts, raw_article_count = fetch_visible_posts()
        previous_ids = set(archive.get("tweets", {}))
        archive_was_empty = not previous_ids
        coverage_event = detect_coverage_event(
            archive_was_empty,
            previous_ids,
            visible_posts,
            raw_article_count,
            now,
        )
        new_count, edited_count = merge_posts(archive, visible_posts, now)

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
