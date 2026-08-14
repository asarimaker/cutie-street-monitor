import json
from pathlib import Path

from xtf import Router

USERNAME = "CUTIE_STREET_"
OUTPUT_FILE = Path("data/latest.json")


def main():
    print(f"Fetching @{USERNAME} timeline...")

    router = Router(
        backend="browser",
        browser_driver="playwright"
    )

    tweets = router.fetch_timeline(USERNAME, limit=20)

    print(f"Retrieved {len(tweets)} tweets")

    results = []

    for tweet in tweets:
        data = tweet.to_dict()
        results.append(data)

        print("---")
        print(json.dumps(data, ensure_ascii=False, indent=2))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "account": USERNAME,
                "count": len(results),
                "tweets": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"Saved {len(results)} tweets to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
