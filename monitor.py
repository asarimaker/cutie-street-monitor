from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://x.com/CUTIE_STREET_"

def main():
    Path("data").mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )

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

        print("Opening:", URL)

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(10000)

        print("Final URL:", page.url)
        print("Title:", page.title())

        body = page.locator("body").inner_text()

        print("BODY START")
        print(body[:5000])
        print("BODY END")

        articles = page.locator("article").count()
        print("Article count:", articles)

        page.screenshot(
            path="data/debug.png",
            full_page=False
        )

        Path("data/debug.txt").write_text(
            f"""URL: {page.url}

TITLE:
{page.title()}

ARTICLE COUNT:
{articles}

BODY:
{body}
""",
            encoding="utf-8"
        )

        browser.close()

if __name__ == "__main__":
    main()
