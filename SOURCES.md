# Sources

Working rule: a source is worth wiring if it yields *specific* video URLs that
yt-dlp can fetch, and if repeated harvests don't return the same 100 things.

## Implemented

| adapter | what you get | notes |
|---|---|---|
| `fourchan` | live webm/mp4 posts from any board | public JSON API, 1 req/sec, threads expire in days; `/wsg/` by default |
| `booru` | video posts from gelbooru-v0.2 / danbooru / e621 sites | auto-detects the API; `soybooru`/danbooru/rule34 are Cloudflare-walled (needs `[booru] cookie`) |
| `reddit` | r/DeepIntoYoutube, r/ObscureMedia, r/InterdimensionalCable, r/VHS, r/lostmedia | needs free script-app creds (client_credentials); paginates top/all |
| `archive` | archive.org advancedsearch → item pages | `--collection prelinger`, `--query 'subject:"ephemeral"'`; best legal well |
| `playlist` | any yt-dlp channel/playlist/search URL | `ytsearch40:...` works as a "give me weird stuff" query |
| `list` | hand-curated URL files | the escape hatch for one-offs |
| `add` | single URLs | any site yt-dlp supports |

Booru sites that answered from this machine: `e621.net`, `xbooru.com`.
Blocked by Cloudflare/CAPTCHA: `soybooru.com`, `danbooru.donmai.us`,
`rule34.xxx`. `soyjak.party` returned 404 on every documented endpoint (site
moved or changed software).

## High-value next adapters

- **jschan/lynxchan imageboards** (soyjak-style) — same catalog/thread JSON
  shape as 4chan; a `--api-base` flag on the fourchan adapter would cover them.
- **archive.org drill-down** — currently one item = one video; many items are
  multi-file reels. Enumerate `files[]` via the metadata API and pick the video.
- **YouTube search with filters** — `ytsearchN:` works now via `playlist`, but
  adding `sp` filter params (upload date < 2012, <1000 views) is what actually
  surfaces the strange stuff.
- **UbuWeb** (`ubu.com/film`) — scrapes cleanly, avant-garde, direct mp4s.
- **Wikimedia Commons** — API category crawl; public-domain industrial/educational.
- **Texas Archive of the Moving Image / AV Geeks / Periscope Film** — local TV,
  ads, training films; mostly YouTube channels, so `playlist` already covers them.
- **Neocities / personal sites** — crawl `.mp4`/`.webm` links from a seed list.
- **Public access TV archives** (Manhattan Neighborhood Network, PhillyCAM) —
  often have their own APIs or YouTube channels.

## Deliberately not wired

- TikTok/Instagram/Twitter: yt-dlp can do it but rate limits and auth rot fast.
- Anywhere that requires a paid API or scraping against a login wall.
- Piracy-adjacent sources: keep the repo to public/archival material so the
  catalog can be shared.

## A note on 4chan/booru content

These are unmoderated uploads. Every item is screened before it enters the
collection: text policy at harvest/probe (`policy/*.txt`), then container,
embedded-payload and ffprobe checks on the finished file, with failures and
flags landing in `data/quarantine/` until `fj approve`. The pipeline cannot tell
you what is in the picture — skip boards/tags you don't trust, and treat
anything you pull as something you chose to keep.

## Harvest etiquette

- 1 download at a time, `--limit` on every harvest, and a hard byte budget.
- Re-running a harvest is safe: dedupe is by canonical URL key, then by sha256.
- Regenerate `collection/index.md` with `bin/fj export` for the provenance record; it stays local (gitignored), never published.
