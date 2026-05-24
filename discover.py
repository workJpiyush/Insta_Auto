"""Query Apify Store directly to find IG follower-related actors."""
import json
import os
import sys

# Force UTF-8 on Windows so emoji in actor titles don't crash
sys.stdout.reconfigure(encoding="utf-8")

import httpx
from dotenv import load_dotenv

load_dotenv()
token = os.environ.get("APIFY_TOKEN")

queries = ["instagram followers", "instagram follower", "ig followers"]
seen = set()

for q in queries:
    r = httpx.get(
        "https://api.apify.com/v2/store",
        params={"search": q, "limit": 30},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    data = r.json()
    items = data.get("data", {}).get("items", [])
    print(f"\n=== Query: '{q}' — {len(items)} results ===")
    for a in items:
        key = f"{a.get('username')}/{a.get('name')}"
        if key in seen:
            continue
        seen.add(key)
        title = a.get("title", "")
        runs = (a.get("stats") or {}).get("totalRuns", "?")
        users = (a.get("stats") or {}).get("totalUsers", "?")
        pricing = a.get("pricingInfos") or []
        ppr = pricing[0].get("pricePerUnitUsd") if pricing else "?"
        unit = pricing[0].get("unitName") if pricing else "?"
        print(f"  {key}")
        print(f"      title:   {title}")
        print(f"      runs:    {runs}  users: {users}")
        print(f"      price:   ${ppr}/{unit}")
