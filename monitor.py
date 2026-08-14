import asyncio
from twikit.guest import GuestClient

USERNAME = "CUTIE_STREET_"

async def main():
    client = GuestClient()

    print("1. Activating guest client...")
    await client.activate()
    print("2. Guest activation succeeded")

    print(f"3. Looking up @{USERNAME}...")
    user = await client.get_user_by_screen_name(USERNAME)

    print("4. User lookup succeeded")
    print("User ID:", user.id)
    print("Name:", user.name)
    print("Screen name:", user.screen_name)

    print("5. Getting tweets...")
    tweets = await client.get_user_tweets(user.id, count=10)

    print(f"6. Retrieved {len(tweets)} tweets")

    for tweet in tweets:
        print(tweet.id, tweet.text[:100])

asyncio.run(main())
