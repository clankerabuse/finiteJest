"""Scanner tests — no network. Generates real media with ffmpeg, then attacks it.

    python3 tests/test_scan.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fj import config, safety

cfg = config.load()
tmp = tempfile.mkdtemp(prefix="fj-scan-")
try:
    # --- a genuine video
    good = os.path.join(tmp, "good.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=320x240:rate=15:duration=2",
                    "-pix_fmt", "yuv420p", good], check=True)
    res = safety.scan(good, cfg)
    assert res.verdict == "ok", (res.verdict, res.reason)
    assert res.container == "mp4" and res.codec == "h264"
    assert res.duration and 1.5 < res.duration < 3, res.duration
    assert res.sha256 and len(res.sha256) == 64

    # --- a zip wearing an .mp4 extension
    fake = os.path.join(tmp, "fake.mp4")
    with zipfile.ZipFile(fake, "w") as z:
        z.writestr("payload.txt", "hello")
    res = safety.scan(fake, cfg)
    assert res.verdict == "fail", res.reason
    assert any("zip" in f for f in res.findings), res.findings

    # --- an html page wearing an .webm extension
    html = os.path.join(tmp, "page.webm")
    with open(html, "w") as f:
        f.write("<!DOCTYPE html><script>alert(1)</script>")
    res = safety.scan(html, cfg)
    assert res.verdict == "fail", res.reason

    # --- a real video with a zip appended (polyglot)
    poly = os.path.join(tmp, "poly.mp4")
    shutil.copy(good, poly)
    with open(poly, "ab") as f:
        f.write(b"PK\x03\x04" + b"\x00" * 64)
    res = safety.scan(poly, cfg)
    assert res.verdict == "fail", res.reason
    assert any("embedded" in f for f in res.findings), res.findings

    # --- audio only
    audio = os.path.join(tmp, "audio.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2", "-c:a", "aac", audio],
                   check=True)
    res = safety.scan(audio, cfg)
    assert res.verdict == "fail", res.reason
    assert any("no video stream" in f for f in res.findings), res.findings

    # --- extension/container mismatch is a flag, not a block
    odd = os.path.join(tmp, "clip.mkv")
    shutil.copy(good, odd)
    res = safety.scan(odd, cfg)
    assert res.verdict in ("ok", "flag"), res.reason

    # --- text policy
    assert safety.screen_text(cfg, "r/DeepIntoYoutube", "weird tv", "")[0] == "ok"
    assert safety.screen_text(cfg, "child porn dump", "", "")[0] == "blocked"
    assert safety.screen_text(cfg, "lolicon", "", "")[0] == "review"
    # word boundaries: no false positive on "childhood"
    assert safety.screen_text(cfg, "childhood memories of public access tv", "", "")[0] == "ok"

    # --- sniff
    assert safety.sniff(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8) == "mp4"
    assert safety.sniff(b"\x1aE\xdf\xa3" + b"\x00" * 12) == "matroska"
    assert safety.sniff(b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 4) == "avi"
    assert safety.sniff(b"not a video at all") is None
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("scan tests: ok")
