"""Scrape followers + profile details for target + comparison accounts.
Saves progressively to data/ so a crash doesn't lose progress."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

import yaml
from instagrapi import Client
from instagrapi.exceptions import (
    ClientError,
    PleaseWaitFewMinutes,
    UserNotFound,
)
from tqdm import tqdm

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


def _followers_file(handle: str) -> Path:
    return DATA_DIR / f"followers_{handle}.json"


def _profiles_file(handle: str) -> Path:
    return DATA_DIR / f"profiles_{handle}.json"


def load_config() -> dict:
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_followers(cl: Client, handle: str, max_n: int, page_delay: float) -> dict:
    """Fetch up to max_n followers of `handle` using paged v1 endpoint.
    Progressive save every 5 pages — safe to Ctrl+C and resume."""
    out_path = _followers_file(handle)
    followers: dict = {}
    if out_path.exists():
        followers = json.loads(out_path.read_text(encoding="utf-8"))
        if len(followers) >= max_n:
            print(f"[=] {handle}: {len(followers)} followers cached, skipping fetch.")
            return followers
        print(f"[+] {handle}: resuming, have {len(followers)}, want {max_n}")

    # Resolve target user via private endpoint
    try:
        user_info = cl.user_info_by_username_v1(handle)
        user_id = int(user_info.pk)
        print(f"[+] {handle}: user_id={user_id} (followers={user_info.follower_count:,})")
    except UserNotFound:
        print(f"[!] {handle}: account not found")
        return followers
    except Exception as e:
        print(f"[!] {handle}: failed to resolve user — {e.__class__.__name__}: {e}")
        return followers

    max_id = ""
    page = 0
    pbar = tqdm(total=max_n, initial=len(followers), desc=f"{handle:>20}", unit="flwr")

    try:
        while len(followers) < max_n:
            page += 1
            try:
                chunk, max_id = cl.user_followers_v1_chunk(
                    user_id, max_amount=200, max_id=max_id
                )
            except PleaseWaitFewMinutes:
                print(f"\n[!] {handle}: rate-limited at page {page} ({len(followers)} fetched). Saving + stopping.")
                break
            except ClientError as e:
                print(f"\n[!] {handle}: client error at page {page}: {e}. Saving + stopping.")
                break
            except Exception as e:
                print(f"\n[!] {handle}: unexpected error at page {page} — {e.__class__.__name__}: {e}")
                break

            if not chunk:
                print(f"\n[+] {handle}: IG returned empty chunk (likely hit IG's hard cap)")
                break

            added = 0
            for u in chunk:
                key = str(u.pk)
                if key not in followers:
                    followers[key] = {
                        "pk": str(u.pk),
                        "username": u.username,
                        "full_name": u.full_name,
                    }
                    added += 1
            pbar.update(added)

            # Progressive save every 5 pages
            if page % 5 == 0:
                out_path.write_text(
                    json.dumps(followers, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            if not max_id:
                print(f"\n[+] {handle}: reached end of followers list")
                break

            time.sleep(page_delay)
    except KeyboardInterrupt:
        print(f"\n[!] {handle}: interrupted at page {page}, {len(followers)} fetched. Saving.")
    finally:
        pbar.close()

    out_path.write_text(
        json.dumps(followers, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[+] {handle}: saved {len(followers)} followers -> {out_path.name}")
    return followers


def fetch_profile_details(
    cl: Client,
    target_handle: str,
    followers: dict,
    delay: float,
) -> dict:
    """For each follower of target, fetch profile info needed for bot/country analysis."""
    out_path = _profiles_file(target_handle)
    profiles: dict = {}
    if out_path.exists():
        profiles = json.loads(out_path.read_text(encoding="utf-8"))

    to_fetch = [uid for uid in followers if uid not in profiles]
    if not to_fetch:
        print(f"[=] All {len(followers)} follower profiles already cached.")
        return profiles

    print(f"[+] Fetching {len(to_fetch)} new follower profiles (sleep {delay}s each)")
    save_every = 25

    for i, uid in enumerate(tqdm(to_fetch, desc="profiles")):
        try:
            info = cl.user_info(int(uid))
            profiles[uid] = {
                "pk": str(info.pk),
                "username": info.username,
                "full_name": info.full_name,
                "biography": info.biography,
                "external_url": str(info.external_url) if info.external_url else None,
                "is_private": info.is_private,
                "is_verified": info.is_verified,
                "media_count": info.media_count,
                "follower_count": info.follower_count,
                "following_count": info.following_count,
                "profile_pic_url": str(info.profile_pic_url) if info.profile_pic_url else None,
                "has_anonymous_profile_picture": getattr(info, "has_anonymous_profile_picture", False),
            }
        except PleaseWaitFewMinutes:
            print(f"\n[!] Rate-limited at {i}/{len(to_fetch)}. Saving + stopping.")
            break
        except (UserNotFound, ClientError) as e:
            profiles[uid] = {"username": followers[uid].get("username"), "error": str(e)}

        if (i + 1) % save_every == 0:
            out_path.write_text(
                json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        time.sleep(delay)

    out_path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Saved {len(profiles)} profiles -> {out_path.name}")
    return profiles


def run_scrape(cl: Client) -> None:
    cfg = load_config()
    target = cfg["target"]
    accounts = [target] + [c["handle"] for c in cfg["comparisons"]]

    print(f"[+] Scraping {len(accounts)} accounts: {accounts}")
    follower_sets = {}
    for handle in accounts:
        follower_sets[handle] = fetch_followers(
            cl, handle, cfg["max_followers_per_account"], cfg["follower_page_delay"]
        )

    if cfg.get("fetch_profile_details", True):
        fetch_profile_details(
            cl, target, follower_sets[target], cfg["profile_fetch_delay"]
        )

    print("[+] Scrape complete.")
