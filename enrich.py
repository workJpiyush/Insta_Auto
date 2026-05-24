"""Run profile enrichment for target's followers (separate actor, separate quota)."""
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import yaml
from apify_client import ApifyClient
from dotenv import load_dotenv

from src.apify_scraper import fetch_profiles

load_dotenv()
client = ApifyClient(os.environ.get("APIFY_TOKEN"))

with open("config.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

target = cfg["target"]
followers = json.loads(
    Path(f"data/followers_{target}.json").read_text(encoding="utf-8")
)
print(f"[+] Enriching {len(followers)} followers of @{target}...")

profiles = fetch_profiles(client, target, followers, batch_size=50)
print(f"[+] Done — {len(profiles)} profiles saved")
