import asyncio
import json
from dotenv import load_dotenv

load_dotenv()

from cards_crawler import search_card_recommendations, search_points_usage
from browser_pool import pool

async def test_cards():
    await pool.initialize()
    try:
        print("Testing Card Recommendations for 'groceries'...")
        result_cards = await search_card_recommendations("groceries")
        print(json.dumps(result_cards, indent=2))
        
        print("\n------------------------------------------------\n")
        
        print("Testing Points Usage for 'Chase Ultimate Rewards' on 'flights to Europe'...")
        result_points = await search_points_usage("Chase Ultimate Rewards", "flights to Europe")
        print(json.dumps(result_points, indent=2))
    finally:
        await pool.teardown()

if __name__ == "__main__":
    asyncio.run(test_cards())
