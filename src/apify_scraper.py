"""Apify-based Instagram follower + profile scraper with multi-token rotation.

Rotates through APIFY_TOKEN1..N from .env. When one hits its free-tier quota
(daily 500/3-runs OR lifetime 1000), automatically switches to the next.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import yaml
from apify_client import ApifyClient
from dotenv import load_dotenv

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# Actor IDs.
FOLLOWER_ACTOR = "scraping_solutions/instagram-scraper-followers-following-no-cookies"
PROFILE_ACTOR = "apify/instagram-profile-scraper"

# Per-token free-tier caps (discovered from actor logs)
PER_TOKEN_DAILY_ITEMS = 500
PER_RUN_MIN = 25       # actor's minimum resultsLimit
PER_RUN_MAX = 500      # daily cap = effective per-run max on free tier


# ---------- Config ----------

def load_config() -> dict:
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- Token rotation ----------

class TokenPool:
    """Pool of Apify tokens with exhaustion tracking + persistence."""

    STATE_FILE = DATA_DIR / "_token_state.json"

    def __init__(self):
        load_dotenv(override=True)
        self.tokens: list[str] = []
        for i in range(1, 20):
            tok = os.environ.get(f"APIFY_TOKEN{i}") or os.environ.get(f"APIFY_TOKEN_{i}")
            if tok:
                self.tokens.append(tok.strip())
        # Fallback: single APIFY_TOKEN
        single = os.environ.get("APIFY_TOKEN")
        if single and single not in self.tokens:
            self.tokens.append(single.strip())

        if not self.tokens:
            sys.exit("ERROR: No APIFY_TOKEN[1-N] / APIFY_TOKEN in .env")

        self.exhausted: set[int] = set()
        self._load_state()
        print(f"[+] Token pool: {len(self.tokens)} tokens loaded, {len(self.exhausted)} marked exhausted")

    def _load_state(self):
        if self.STATE_FILE.exists():
            try:
                state = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
                self.exhausted = set(state.get("exhausted", []))
            except Exception:
                pass

    def _save_state(self):
        self.STATE_FILE.write_text(
            json.dumps({"exhausted": sorted(self.exhausted)}, indent=2),
            encoding="utf-8",
        )

    def get_client(self) -> tuple[Optional[ApifyClient], Optional[int]]:
        """Return (client, token_index) for next usable token, or (None, None) if all exhausted."""
        for i in range(len(self.tokens)):
            if i in self.exhausted:
                continue
            return ApifyClient(self.tokens[i]), i
        return None, None

    def mark_exhausted(self, idx: int, reason: str = ""):
        if idx not in self.exhausted:
            self.exhausted.add(idx)
            self._save_state()
            print(f"[~] Token #{idx + 1} marked exhausted ({reason})")

    def reset(self):
        self.exhausted.clear()
        self._save_state()
        print("[~] Token pool reset — all tokens marked available")


# ---------- Followers ----------

def _save_followers(handle: str, followers: dict):
    p = DATA_DIR / f"followers_{handle}.json"
    p.write_text(json.dumps(followers, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_followers(handle: str) -> dict:
    p = DATA_DIR / f"followers_{handle}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def fetch_followers(pool: TokenPool, handle: str, max_n: int) -> dict:
    """Fetch up to max_n followers of `handle`. Rotates tokens when quota hits.
    Returns dict {pk: {username, full_name, ...}}."""
    followers = _load_followers(handle)
    if len(followers) >= max_n:
        print(f"[=] {handle}: {len(followers)} cached, skip")
        return followers
    if followers:
        print(f"[+] {handle}: have {len(followers)} cached, want {max_n}")

    while len(followers) < max_n:
        client, idx = pool.get_client()
        if client is None:
            print(f"[!] {handle}: all tokens exhausted at {len(followers)}/{max_n}")
            break

        needed = max_n - len(followers)
        chunk = max(PER_RUN_MIN, min(needed, PER_RUN_MAX))

        print(f"  → token #{idx + 1}: requesting {chunk} for {handle}...")
        run_input = {
            "Account": [handle],
            "resultsLimit": chunk,
            "dataToScrape": "Followers",
        }
        try:
            run = client.actor(FOLLOWER_ACTOR).call(run_input=run_input)
        except Exception as e:
            err = str(e).lower()
            if "quota" in err or "limit" in err or "blocked" in err:
                pool.mark_exhausted(idx, f"call failed: {e.__class__.__name__}")
                continue
            print(f"  [!] token #{idx + 1}: unexpected error {e}")
            pool.mark_exhausted(idx, f"error: {e.__class__.__name__}")
            continue

        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        status = run.get("status", "")
        print(f"  ← token #{idx + 1}: got {len(items)} items (run status: {status})")

        if not items:
            # Likely quota hit — actor returned 0 due to free-tier limit
            pool.mark_exhausted(idx, "zero items returned")
            continue

        new_count = 0
        for item in items:
            pk = str(item.get("id") or item.get("pk") or item.get("username"))
            if pk in followers:
                continue
            followers[pk] = {
                "pk": pk,
                "username": item.get("username"),
                "full_name": item.get("full_name"),
                "is_private": item.get("is_private", False),
                "is_verified": item.get("is_verified", False),
                "profile_pic_url": item.get("profile_pic_url"),
            }
            new_count += 1

        _save_followers(handle, followers)
        print(f"  ✓ {handle}: +{new_count} new, total {len(followers)}/{max_n}")

        # If actor returned fewer items than requested → daily quota likely depleted
        if len(items) < chunk:
            pool.mark_exhausted(idx, f"got {len(items)} < requested {chunk}")
        # If 0 new items (all duplicates because actor returns same top-N each call)
        # → this actor can't paginate further with this token; skip to next.
        elif new_count == 0:
            pool.mark_exhausted(idx, "no new items (actor returned duplicates)")
            # Also break the per-account loop — no other token will paginate either
            print(f"  [!] {handle}: actor doesn't paginate beyond top-{len(followers)}. Stopping.")
            break

        # Brief polite delay between runs
        time.sleep(2)

    return followers


# ---------- Profile enrichment ----------

def _normalize_profile(item: dict) -> dict:
    pic = item.get("profilePicUrl") or item.get("profile_pic_url") or ""
    default_pfp = bool(pic) and (
        "anonymousUser" in pic or "default_profile" in pic
    )
    return {
        "pk": str(item.get("id") or item.get("pk") or ""),
        "username": item.get("username"),
        "full_name": item.get("fullName") or item.get("full_name"),
        "biography": item.get("biography"),
        "external_url": item.get("externalUrl") or item.get("external_url"),
        "is_private": item.get("private", item.get("is_private", False)),
        "is_verified": item.get("verified", item.get("is_verified", False)),
        "media_count": item.get("postsCount") or item.get("media_count") or 0,
        "follower_count": item.get("followersCount") or item.get("follower_count") or 0,
        "following_count": item.get("followsCount") or item.get("following_count") or 0,
        "profile_pic_url": pic,
        "has_anonymous_profile_picture": default_pfp,
    }


def fetch_profiles(pool: TokenPool, target_handle: str, follower_dict: dict, batch_size: int = 50) -> dict:
    out_path = DATA_DIR / f"profiles_{target_handle}.json"
    profiles = {}
    if out_path.exists():
        profiles = json.loads(out_path.read_text(encoding="utf-8"))

    uname_to_pk = {info["username"]: pk for pk, info in follower_dict.items() if info.get("username")}
    to_fetch = [u for u in uname_to_pk if uname_to_pk[u] not in profiles]

    if not to_fetch:
        print(f"[=] All {len(follower_dict)} profiles cached")
        return profiles

    total_batches = (len(to_fetch) - 1) // batch_size + 1
    print(f"[+] Enriching {len(to_fetch)} profiles in {total_batches} batches of {batch_size}")

    # Profile actor uses $5 credit, not "free trial" — typically each account has $5
    # so 5 tokens × $5 ÷ $2.30/1k ≈ 10,800 profiles. We won't hit limits for our scope.
    # Use first available token; rotate if it errors.
    for bi, i in enumerate(range(0, len(to_fetch), batch_size), 1):
        batch = to_fetch[i : i + batch_size]
        print(f"  → batch {bi}/{total_batches} ({len(batch)} usernames)")

        success = False
        while not success:
            client, idx = pool.get_client()
            if client is None:
                print(f"  [!] all tokens exhausted at batch {bi}; stopping enrichment")
                return profiles

            try:
                run = client.actor(PROFILE_ACTOR).call(run_input={"usernames": batch})
                items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
                if not items:
                    pool.mark_exhausted(idx, "profile actor returned 0")
                    continue
                for item in items:
                    uname = item.get("username")
                    pk = uname_to_pk.get(uname) or str(item.get("id") or uname)
                    profiles[pk] = _normalize_profile(item)
                out_path.write_text(
                    json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"  ✓ batch {bi} (token #{idx + 1}): {len(items)} returned, total {len(profiles)}")
                success = True
            except Exception as e:
                print(f"  [!] batch {bi} token #{idx + 1} failed: {e}. Rotating.")
                pool.mark_exhausted(idx, f"profile error: {e.__class__.__name__}")

    print(f"[+] Saved {len(profiles)} profiles -> {out_path.name}")
    return profiles


# ---------- Entry point ----------

def run_scrape() -> None:
    cfg = load_config()
    target = cfg["target"]
    accounts = [target] + [c["handle"] for c in cfg["comparisons"]]

    # Allow target_followers_override to give CJP more followers than comparisons
    default_max = cfg["max_followers_per_account"]
    target_max = cfg.get("target_followers_override", default_max)

    print(f"[+] Apify multi-token scrape — {len(accounts)} accounts")
    for h in accounts:
        n = target_max if h == target else default_max
        print(f"    @{h}: target {n} followers")
    print()

    pool = TokenPool()

    for handle in accounts:
        n = target_max if handle == target else default_max
        print(f"\n=== @{handle} (target {n}) ===")
        fetch_followers(pool, handle, n)

    # Profile enrichment for target only
    if cfg.get("fetch_profile_details", True):
        print(f"\n{'=' * 60}\n[+] Profile enrichment for @{target}\n{'=' * 60}")
        followers = _load_followers(target)
        fetch_profiles(pool, target, followers, batch_size=50)

    print("\n[+] Scrape complete.")
