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
bin/fj harvest fourchan wsg --limit 40 --threads 6      # live webm/mp4 dumps
bin/fj harvest booru https://e621.net --tags "webm" --limit 30
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
| `harvest fourchan BOARD` | scan live threads for webm/mp4 (`--threads`, `--filter "webm dump"`) |
| `harvest booru URL` | gelbooru-v0.2 / danbooru / e621 API (`--tags`, `--page`) |
| `harvest archive` | archive.org search (`--query`, `--collection`, `--sort`) |
| `harvest playlist URL` | any channel/playlist/search URL yt-dlp understands |
| `harvest list FILE` | one URL per line |
| `probe` | fetch metadata, apply the gate, mark rejects with a reason |
| `fetch` | download queued rows; stops at `--count` and `--budget` (`--dry-run`, `--id N` to spot-check) |
| `ls` | list rows (`--status`, `-e` for reasons, `--json`) |
| `stats` | counts, bytes, per-source totals |
| `verify` | check files still exist / sizes match |
| `scan` | re-run the byte scanner on files already in the collection |
| `review` | list quarantined files with reasons (`--frames` for stills) |
| `approve` / `reject` | promote / discard a quarantined file |
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

## Safety screening

Every download is screened before it can enter the collection (`fj/safety.py`),
because 4chan and boorus are unmoderated.

- **text** — `policy/block_terms.txt` is a hard block: matching items are never
  fetched and are recorded as `blocked` with the term that caught them.
  `policy/flag_terms.txt` queues normally but guarantees a quarantine later.
  Both match title, tags and notes, at harvest time and again after probing.
- **bytes** — container sniffing (a zip/HTML/ELF wearing `.mp4` fails), an
  embedded-payload scan (anything appended to a real video fails), ffprobe
  structure (must contain a video stream, sane dimensions, real duration),
  bitrate sanity, and the 5-minute cap re-read from the file itself.

Failures and flags move to `data/quarantine/` with status `quarantined` and stay
out of `collection/index.*` until you act:

```bash
bin/fj review              # what's waiting, and why
bin/fj review --frames     # + three extracted stills per file
bin/fj approve 12          # move into the collection, mark done, re-export
bin/fj reject 12 --purge   # delete it, record the block
bin/fj scan --quarantine   # re-scan everything already collected
```

`[safety] require_review = true` makes *every* download wait for approval.

What this cannot do: tell you what is in the picture. Detecting illegal imagery
needs hash matching against NCMEC/PhotoDNA-style databases, which is an
organisational partnership rather than a library. The scanner catches malformed,
disguised, audio-only, over-long and text-flagged files and makes a human look
at anything doubtful — source hygiene is still the primary control.

## 4chan + boorus

**4chan** uses the public JSON API (`a.4cdn.org`): catalog → threads → any post
with `tim`+`ext` of `.webm`/`.mp4` becomes a candidate, media straight from
`i.4cdn.org`. One request per second, and threads expire within days, so harvest
often — the url_key makes repeats free. `/wsg/` is nominally worksafe but
reposts from other boards; pick boards you trust, because nothing downstream can
judge content for you.

```
bin/fj harvest fourchan wsg --limit 60 --threads 8
bin/fj harvest fourchan wsg --filter "webm dump" --limit 100
```

Set default boards under `[fourchan] boards = [...]` in `config.toml`.

**Boorus** expose a post = file_url + tags; the adapter auto-detects the API
flavour (danbooru `posts.json`, gelbooru v0.2 `dapi`, e621 `posts.json`) and
keeps only video files (`webm/mp4/m4v/mov`), scanning a few pages because most
results are stills.

```
bin/fj harvest booru https://e621.net --tags "webm" --limit 40
bin/fj harvest booru https://xbooru.com --tags "webm" --limit 40
bin/fj harvest booru https://danbooru.donmai.us --tags "filetype:webm" --limit 40
```

`soybooru.com`, danbooru and rule34 sit behind Cloudflare and answer 403 to any
script (verified). If you have a browser session, paste the raw `Cookie:` header
into `[booru] cookie` and requests will carry it; otherwise `gallery-dl`-style
cookie export is the only way in. The adapter is ready for it either way.

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
