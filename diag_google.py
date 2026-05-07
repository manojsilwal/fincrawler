"""diag_google.py — diagnose what Google Shopping actually returns."""
import asyncio, os, urllib.parse
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

async def run():
    from browser_pool import pool
    from stealth import apply_stealth, get_stealth_context_kwargs

    await pool.initialize()
    query = "DJI Osmo Pocket 3"
    url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&tbm=shop&hl=en&gl=us"

    context = await pool._browser.new_context(**get_stealth_context_kwargs())
    page = await context.new_page()
    await apply_stealth(page)

    print(f"Loading: {url}")
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    print(f"HTTP: {resp.status if resp else '?'}")
    await page.wait_for_timeout(4000)

    # Check elements
    for sel in [".sh-dgr__grid-result", ".sh-pr__product-results", "[data-hveid]", ".g", "h3", "a"]:
        n = await page.locator(sel).count()
        print(f"  {sel}: {n} elements")

    raw = await page.inner_text("body")
    print(f"\nBody chars: {len(raw)}")
    print(f"\n--- First 3000 chars ---\n{raw[:3000]}")

    await context.close()
    await pool.teardown()

asyncio.run(run())
