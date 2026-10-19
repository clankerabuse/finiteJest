# finiteJest

A farm for strange video. Point it at feeds, archives and playlists; it keeps a
manifest, downloads the good ones, and hard-stops at a size and count budget.
Goal for v1: **~100 videos / ~10 GiB**, from as many different sources as possible.

```
harvest ─► candidate rows ─► probe (metadata + quality gate) ─► download ─► verify
   │                                                              │
   └── reddit, archive.org, any yt-dlp playlist, url lists         └── sha256, ffprobe
```

## Quickstart

```bash
cd ~/Projects/finiteJest
bin/fj init
bin/fj harvest archive --collection prelinger --limit 50
bin/fj harvest playlist "https://www.youtube.com/@SomeWeirdChannel/videos" --limit 40
bin/fj fetch --dry-run          # see what passes the gate
bin/fj fetch                    # download until 100 videos / 10 GiB
bin/fj stats
bin/fj export                   # writes collection/index.{md,json} — commit these
```

Add one-offs by hand:

```bash
bin/fj add "https://www.youtube.com/watch?v=..." --tag deepintoyoutube
echo "https://archive.org/details/whatever" >> queue/links.txt
bin/fj add --file queue/links.txt --source list
```

## Commands

| command | what it does |
|---|---|
| `init` | create dirs + sqlite db |
| `add URL...` | queue specific URLs (`--probe` to enrich now) |
| `harvest reddit SUB` | pull posts from a subreddit (`--sort top --timeframe all`) |
| `harvest archive` | archive.org search (`--query`, `--collection`, `--sort`) |
| `harvest playlist URL` | any channel/playlist/search URL yt-dlp understands |
| `harvest list FILE` | one URL per line |
| `probe` | fetch metadata, apply the gate, mark rejects with a reason |
| `fetch` | download queued rows; stops at `--count` and `--budget` (`--dry-run`, `--id N` to spot-check) |
| `ls` | list rows (`--status`, `-e` for reasons, `--json`) |
| `stats` | counts, bytes, per-source totals |
| `verify` | check files still exist / sizes match |
| `requeue` | put failed/skipped rows back in the queue |
| `export` | write the committable catalog |
| `show` / `rm` | inspect / remove a row (`rm --purge` deletes the file) |

## Quality gate (config.toml)

- duration between 3s and 5m (the cap is enforced twice: at probe, and again
  from ffprobe after download for extractors that hide the runtime)
- nothing live, private, DRM'd or audio-only
- estimated file ≤ 800 MiB, format capped at 1080p h264/aac → mp4 remux
- per-run budget: refuses anything that would push the collection over 10 GiB
- exact-duplicate bytes (sha256) are discarded even if the link differs

Everything the gate rejects stays in the db with a reason, so `fj ls -e` is a
record of what you passed on.

## Reddit

Reddit now 403s anonymous JSON from most IPs. Create a free **script** app at
<https://www.reddit.com/prefs/apps>, then:

```toml
[reddit]
client_id = "..."
client_secret = "..."
user_agent = "linux:finitejest:0.1 (personal video archive)"
```

`fj harvest reddit DeepIntoYoutube --limit 100` will then page through the
listing with OAuth's client_credentials grant (no user login needed).
Env vars `FJ_REDDIT_ID` / `FJ_REDDIT_SECRET` override the file.

## Layout

```
config.toml        budgets, format, creds
fj/                the pipeline (cli, db, ytdlp, gate, sources/)
data/videos/<src>/ media, gitignored  (10 GiB cap, ~100 MB avg)
data/meta/         yt-dlp .info.json sidecars
data/finitejest.db manifest (sqlite)
collection/        index.md + index.json — the part that goes in git
queue/             drop-in link lists
```

Media is deliberately **not** in git: 10 GiB of mp4 in a repo is unusable.
The repo tracks the pipeline + catalog (titles, source URLs, sizes, sha256), so
the collection is reproducible and shareable; add a torrent or IPFS step when
you actually want to ship bytes.

## Notes / next

See `SOURCES.md` for the source shortlist and which adapters still need wiring
(tiktok/vimeo/4chan-shaped things, ubuweb, wikimedia, local-TV archives).
