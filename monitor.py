import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from twikit.guest import Client

USERNAME = "CUTIE_STREET_"
OUTPUT_FILE = Path("data/latest.json")


def now_jst():
    return datetime.now(timezone(timedelta(hours=9)))


async def main():
    generated_at = now_jst()
    cutoff = generated_at - timedelta(hours=24)

    output = {
        "status": "error",
        "account": USERNAME,
        "generated_at": generated_at.isoformat(),
        "tweets": [],
        "error": None,
    }

    try:
        client = Client()

        user = await client.get_user_by_screen_name(USERNAME)
        tweets = await user.get_tweets("Tweets", count=50)

        results = []

        for tweet in tweets:
            created_at = tweet.created_at_datetime

            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            created_at_jst = created_at.astimezone(
                timezone(timedelta(hours=9))
            )

            if created_at_jst < cutoff:
                continue

            media = getattr(tweet, "media", None) or []

            images = []
            videos = []

            for item in media:
                media_type = getattr(item, "type", "")

                if media_type == "photo":
                    url = getattr(item, "media_url_https", None)
                    if url:
                        images.append(url)

                elif media_type in ("video", "animated_gif"):
                    url = getattr(item, "media_url_https", None)
                    if url:
                        videos.append(url)

            tweet_type = "tweet"

            if getattr(tweet, "retweeted_tweet", None):
                tweet_type = "retweet"
            elif getattr(tweet, "quoted_tweet", None):
                tweet_type = "quote"
            elif getattr(tweet, "in_reply_to", None):
                tweet_type = "reply"

            results.append(
                {
                    "id": str(tweet.id),
                    "created_at": created_at_jst.isoformat(),
                    "text": tweet.text,
                    "type": tweet_type,
                    "images": images,
                    "videos": videos,
                    "url": f"https://x.com/{USERNAME}/status/{tweet.id}",
                }
            )

        results.sort(key=lambda x: x["created_at"])

        output["status"] = "success"
        output["tweets"] = results
        output["error"] = None

    except Exception as e:
        output["status"] = "error"
        output["error"] = f"{type(e).__name__}: {e}"

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps(output, ensure_ascii=False, indent=2))


asyncio.run(main())
