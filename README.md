# finiteJest

A farm for strange video. Point it at feeds, archives and playlists; it keeps a
manifest, downloads the good ones, and hard-stops at a size and count budget.
Goal for v1: **~100 videos / ~10 GiB** from as many sources as possible.

```
harvest ─► candidates ─► probe + quality gate ─► download ─► verify (sha256, ffprobe)
             ▲
   reddit, 4chan, boorus, archive.org, any yt-dlp playlist, url lists
```

## Quickstart

```bash
bin/fj init
bin/fj harvest fourchan wsg --limit 40 --threads 6
bin/fj harvest booru https://e621.net --tags webm --limit 30
bin/fj harvest archive --collection prelinger --limit 50
bin/fj harvest playlist "https://www.youtube.com/@SomeWeirdChannel/videos" --limit 40
bin/fj fetch --dry-run     # what passes the gate
bin/fj fetch               # download until 100 videos / 10 GiB
bin/fj stats
bin/fj export              # collection/index.{md,json}
```

`bin/fj --help` lists every command. `fj ls -e` is the record of what the gate
rejected and why; `fj review`, `approve` and `reject` handle anything
quarantined.

## Gate and screening

Duration 3s–5m, nothing live/private/DRM'd/audio-only, estimated file ≤800 MiB,
capped at 1080p h264/aac mp4, collection budget 10 GiB, and exact-duplicate bytes
discarded even when the link differs.

Because 4chan and boorus are unmoderated, every download is screened before it
can enter the collection: `policy/block_terms.txt` is a hard block,
`policy/flag_terms.txt` forces a quarantine later, and the bytes are checked for
container sniffing, appended payloads and real ffprobe structure. It cannot tell
you what is in a picture — source hygiene is still the primary control.

Budgets, format, boards and credentials live in `config.toml`; [SOURCES.md](SOURCES.md)
lists sources and which adapters still need wiring.

Media and the exported catalog are gitignored, and a `pre-commit` hook plus a
`no-media` Action keep them out of the tree.

## License

MIT — see [LICENSE](LICENSE).
