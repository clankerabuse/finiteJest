# Sources

Working rule: a source is worth wiring if it yields *specific* video URLs that
yt-dlp can fetch, and if repeated harvests don't return the same 100 things.

## Implemented

| adapter | what you get | notes |
|---|---|---|
| `reddit` | r/DeepIntoYoutube, r/ObscureMedia, r/InterdimensionalCable, r/VHS, r/lostmedia | needs free script-app creds (client_credentials); paginates top/all |
| `archive` | archive.org advancedsearch → item pages | `--collection prelinger`, `--query 'subject:"ephemeral"'`; best legal well |
| `playlist` | any yt-dlp channel/playlist/search URL | `ytsearch40:...` works as a "give me weird stuff" query |
| `list` | hand-curated URL files | the escape hatch for one-offs |
| `add` | single URLs | any site yt-dlp supports |

## High-value next adapters

- **archive.org drill-down** — currently one item = one video; many items are
  multi-file reels. Enumerate `files[]` via the metadata API and pick the video.
- **YouTube search with filters** — `ytsearchN:` works now via `playlist`, but
  adding `sp` filter params (upload date < 2012, <1000 views) is what actually
  surfaces the strange stuff.
- **UbuWeb** (`ubu.com/film`) — scrapes cleanly, avant-garde, direct mp4s.
- **Wikimedia Commons** — API category crawl; public-domain industrial/educational.
- **Texas Archive of the Moving Image / AV Geeks / Periscope Film** — local TV,
  ads, training films; mostly YouTube channels, so `playlist` already covers them.
- **Vimeo staff-picks-era channels** — `playlist` covers it; most are still up.
- **Neocities / personal sites** — crawl `.mp4`/`.webm` links from a seed list.
- **Public access TV archives** (Manhattan Neighborhood Network, PhillyCAM) —
  often have their own APIs or YouTube channels.
- **4chan /b/ webm dumps** — no API; would need thread scraping + dedupe by hash.
  Only worth it if you want the truly cursed end of the spectrum.

## Deliberately not wired

- TikTok/Instagram/Twitter: yt-dlp can do it but rate limits and auth rot fast.
- Anywhere that requires a paid API or scraping against a login wall.
- Piracy-adjacent sources: keep the repo to public/archival material so the
  catalog can be shared.

## Harvest etiquette

- 1 download at a time, `--limit` on every harvest, and a hard byte budget.
- Re-running a harvest is safe: dedupe is by canonical URL key, then by sha256.
- Keep `collection/index.md` in git; it's the provenance record.
