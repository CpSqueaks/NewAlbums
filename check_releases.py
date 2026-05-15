#!/usr/bin/env python3
"""
Deezer album-release alerter.

Watches a list of artists (artists.txt) and posts to a Discord webhook
when any of them release a new album. Singles, EPs, and compilations are
filtered out via Deezer's `record_type` field.

State files (committed back to the repo by the GitHub Actions workflow):
  - artists.txt        Input. One artist name per line.
  - artist_ids.json    Cache of artist name -> Deezer artist ID.
  - seen.json          History of album IDs we've already alerted on.

Required environment variable:
  - DISCORD_WEBHOOK    Discord channel webhook URL.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests

DEEZER_API = "https://api.deezer.com"
LOOKBACK_DAYS = 14
EMBED_COLOR = 0x9D3AFF
DEEZER_DELAY = 0.2
DISCORD_DELAY = 0.5
REQUEST_TIMEOUT = 15

ARTISTS_FILE = "artists.txt"
IDS_FILE = "artist_ids.json"
SEEN_FILE = "seen.json"


def load_artists():
    with open(ARTISTS_FILE, encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.lstrip().startswith("#")
        ]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def deezer_get(path, params=None):
    r = requests.get(f"{DEEZER_API}{path}", params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def resolve_artist(name):
    try:
        data = deezer_get("/search/artist", {"q": name, "limit": 1})
    except requests.RequestException as e:
        print(f"  ERROR searching '{name}': {e}", file=sys.stderr)
        return None, None
    hits = data.get("data") or []
    if not hits:
        return None, None
    return hits[0]["id"], hits[0]["name"]


def fetch_albums(artist_id):
    try:
        data = deezer_get(f"/artist/{artist_id}/albums", {"limit": 200})
    except requests.RequestException as e:
        print(f"  ERROR fetching albums for artist {artist_id}: {e}", file=sys.stderr)
        return []
    return data.get("data") or []


def is_recent(release_date_str):
    if not release_date_str:
        return False
    try:
        rd = datetime.strptime(release_date_str, "%Y-%m-%d")
    except ValueError:
        return False
    delta = datetime.utcnow() - rd
    return timedelta(0) <= delta <= timedelta(days=LOOKBACK_DAYS)


def is_future_release(release_date_str):
    if not release_date_str:
        return False
    try:
        rd = datetime.strptime(release_date_str, "%Y-%m-%d")
    except ValueError:
        return False
    return rd.date() > datetime.utcnow().date()


def build_embed(artist_name, album):
    title = album.get("title", "Unknown")
    deezer_url = album.get("link") or f"https://www.deezer.com/album/{album['id']}"
    spotify_url = (
        "https://open.spotify.com/search/"
        + quote_plus(f"{artist_name} {title}")
    )
    cover = (
        album.get("cover_xl")
        or album.get("cover_big")
        or album.get("cover_medium")
        or ""
    )
    return {
        "title": f"{artist_name} — {title}",
        "url": deezer_url,
        "color": EMBED_COLOR,
        "fields": [
            {
                "name": "Released",
                "value": album.get("release_date", "unknown"),
                "inline": True,
            },
            {
                "name": "Listen",
                "value": f"[Deezer]({deezer_url}) · [Spotify]({spotify_url})",
                "inline": True,
            },
        ],
        "image": {"url": cover} if cover else {},
    }


def post_discord(webhook_url, embed):
    """Returns True on successful post, False on failure."""
    try:
        r = requests.post(
            webhook_url,
            json={"embeds": [embed]},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  ERROR posting to Discord: {e}", file=sys.stderr)
        return False


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK")
    if not webhook:
        print("ERROR: DISCORD_WEBHOOK environment variable not set.", file=sys.stderr)
        sys.exit(1)

    artists = load_artists()
    print(f"Loaded {len(artists)} artists from {ARTISTS_FILE}.")

    artist_ids = load_json(IDS_FILE, {})
    seen_existed = os.path.exists(SEEN_FILE)
    seen = set(load_json(SEEN_FILE, []))

    unresolved = [a for a in artists if a not in artist_ids]
    not_found = []
    if unresolved:
        print(f"Resolving {len(unresolved)} new artist(s) on Deezer...")
        for name in unresolved:
            aid, canonical = resolve_artist(name)
            if aid:
                print(f"  '{name}' -> {canonical} (id={aid})")
                artist_ids[name] = aid
            else:
                print(f"  '{name}' -> NOT FOUND")
                artist_ids[name] = None
                not_found.append(name)
            time.sleep(DEEZER_DELAY)

    artist_ids = {k: v for k, v in artist_ids.items() if k in artists}
    save_json(IDS_FILE, artist_ids)

    # Walk every artist; collect any album we haven't seen before.
    # IMPORTANT: for albums we intend to alert on, we defer adding to
    # seen_after until AFTER a successful Discord post, so a transient
    # post failure can be retried on the next run.
    new_albums = []
    seen_after = set(seen)
    print(f"Checking {sum(1 for v in artist_ids.values() if v)} resolved artist(s)...")
    for name in artists:
        aid = artist_ids.get(name)
        if not aid:
            continue
        for album in fetch_albums(aid):
            if album.get("record_type") != "album":
                continue
            album_id = str(album.get("id"))
            if not album_id:
                continue
            release_date_str = album.get("release_date")
            if is_future_release(release_date_str):
                seen_after.discard(album_id)
                print(
                    f"  Skipping future release: {name} - "
                    f"{album.get('title')} ({release_date_str})"
                )
                continue
            if album_id in seen:
                continue
            if not seen_existed:
                seen_after.add(album_id)
                continue
            if not is_recent(release_date_str):
                seen_after.add(album_id)
                continue
            new_albums.append((name, album))
        time.sleep(DEEZER_DELAY)

    if not seen_existed:
        save_json(SEEN_FILE, sorted(seen_after))
        print(
            f"First run: recorded {len(seen_after)} albums as baseline. "
            "No alerts sent. Future runs will post about new releases only."
        )
        if not_found:
            print(f"\nArtists Deezer couldn't resolve ({len(not_found)}):")
            for n in not_found:
                print(f"  - {n}")
        return

    if not new_albums:
        print(f"No new albums in the last {LOOKBACK_DAYS} days.")
    else:
        # Dedupe within this run by (artist, title, release_date).
        seen_in_run = set()
        unique_albums = []
        for name, album in new_albums:
            key = (
                name.lower(),
                (album.get("title") or "").lower(),
                album.get("release_date") or "",
            )
            if key in seen_in_run:
                print(f"  Skipping duplicate in run: {name} - {album.get('title')}")
                seen_after.add(str(album["id"]))
                continue
            seen_in_run.add(key)
            unique_albums.append((name, album))

        print(f"Found {len(unique_albums)} new album(s). Posting to Discord...")
        for name, album in unique_albums:
            print(
                f"  -> {name} - {album.get('title')} "
                f"(released {album.get('release_date')})"
            )
            if post_discord(webhook, build_embed(name, album)):
                seen_after.add(str(album["id"]))
            else:
                print("     (NOT marking as seen due to failed post; will retry next run)")
            time.sleep(DISCORD_DELAY)

    save_json(SEEN_FILE, sorted(seen_after))

    if not_found:
        print(f"\nArtists Deezer couldn't resolve ({len(not_found)}):")
        for n in not_found:
            print(f"  - {n}")

    print("Done.")


if __name__ == "__main__":
    main()
