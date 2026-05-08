# Deezer Album Release Alerter

Watches a list of artists on Deezer and posts to a Discord channel when any of
them release a new **album**. Singles, EPs, and compilations are filtered out.

Runs as a GitHub Actions cron job — no server, no laptop required.

## How it works

1. Reads the artist list from `artists.txt`.
2. Looks each one up on Deezer and caches the artist ID in `artist_ids.json`.
3. Fetches each artist's discography and keeps only entries where
   `record_type == "album"`.
4. Compares against `seen.json` (the running record of albums already alerted on).
5. Posts new releases (within the last 14 days) to Discord as embeds with
   cover art, release date, and Deezer + Spotify search links.
6. Commits the updated state files back to the repo so the next run remembers.

## Setup (one time)

### 1. Create the repo

Make a new GitHub repo (private is fine) and put these files inside it,
preserving the layout:

```
.
├── README.md
├── artists.txt
├── check_releases.py
├── requirements.txt
└── .github/
    └── workflows/
        └── check.yml
```

### 2. Add the Discord webhook as a repo secret

Repo → **Settings** → **Secrets and variables** → **Actions** → **New
repository secret**.

- **Name:** `DISCORD_WEBHOOK`
- **Value:** paste your Discord webhook URL

### 3. Allow the workflow to commit back to the repo

Repo → **Settings** → **Actions** → **General** → scroll to **Workflow
permissions** → choose **Read and write permissions** → **Save**.

This lets the action push updated `artist_ids.json` and `seen.json` back to the
repo at the end of each run.

### 4. Run it once manually (the baseline run)

Repo → **Actions** tab → **Check Deezer for new albums** → **Run workflow**.

The first run records every existing album from every listed artist as the
baseline and **does not post any alerts**. After that, only newly discovered
albums released in the last 14 days will trigger Discord posts.

From then on, the workflow runs automatically every Friday at 14:00 UTC
(7:00 AM PDT / 8:00 AM PST).

## Maintenance

### Add or remove artists

Edit `artists.txt` and commit. New artists are auto-resolved on the next run.
Removed artists are silently dropped from the cache.

### Fix a wrong artist match

Search hits aren't always perfect — for ambiguous names like *Halifax* or
*Filter*, Deezer's top result might be the wrong artist. After the first run,
look at `artist_ids.json`. If anything is wrong, find the correct Deezer
artist ID (the number in `https://www.deezer.com/artist/12345`) and edit the
file. The script will use whatever IDs it finds there.

### Tune the lookback window

`LOOKBACK_DAYS` near the top of `check_releases.py` controls how recent a
release has to be to trigger an alert. Default is 14 days, which gives plenty
of slack if a Friday run gets skipped.

### Change the schedule

Edit the `cron:` line in `.github/workflows/check.yml`. Times are UTC.
Cron format is `minute hour day-of-month month day-of-week`.

## Local testing

```sh
pip install -r requirements.txt
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..." python check_releases.py
```

The first local run will create `seen.json` as a baseline (no Discord posts).
Subsequent runs will post any new albums.

## Troubleshooting

- **No alerts ever:** check the Actions tab for the latest run's logs. The
  script prints what it resolved, what it found, and what (if anything) it
  posted.
- **Wrong band gets posted:** see "Fix a wrong artist match" above.
- **Workflow can't push to repo:** double-check step 3 above (Read and write
  permissions).
- **Webhook errors:** regenerate the Discord webhook and update the repo secret.
