"""Sanity tests with no deps: python3 tests/test_util.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fj.util import canonical_key, human_size, parse_size
from fj import pipeline, config

# --- sizes
assert parse_size("15G") == 15 * 1024 ** 3
assert parse_size("800m") == 800 * 1024 ** 2
assert parse_size("1.5G") == int(1.5 * 1024 ** 3)
assert parse_size(1024) == 1024
assert human_size(15 * 1024 ** 3) == "15.0 GiB"
assert human_size(0) == "0 B"

# --- url identity
cases = [
    ("https://www.youtube.com/watch?v=abc123XYZ_-&t=42", "https://youtu.be/abc123XYZ_-", True),
    ("https://youtube.com/watch?v=abc123XYZ_-&utm_source=x", "https://www.youtube.com/watch?v=abc123XYZ_-", True),
    ("https://archive.org/details/SomeItem/", "https://archive.org/details/SomeItem", True),
    ("https://archive.org/details/SomeItem", "https://archive.org/details/OtherItem", False),
    ("https://example.com/a/?utm_source=x&id=2", "https://example.com/a?id=2", True),
]
for a, b, same in cases:
    ka, kb = canonical_key(a), canonical_key(b)
    assert (ka == kb) is same, (a, b, ka, kb)

# --- gate
cfg = config.load()
keep = {"duration": 300, "ext": "mp4", "vcodec": "avc1", "filesize": 50 * 1024 ** 2}
assert pipeline.gate(keep, cfg)[0] == "keep"           # exactly 5:00 is allowed
assert pipeline.gate({**keep, "duration": 301}, cfg)[0] == "reject"
assert pipeline.gate({**keep, "duration": 7200}, cfg)[0] == "reject"
assert pipeline.gate({**keep, "is_live": True}, cfg)[0] == "reject"
assert pipeline.gate({**keep, "ext": "m4a", "vcodec": "none"}, cfg)[0] == "reject"
assert pipeline.gate({**keep, "filesize": 900 * 1024 ** 2}, cfg)[0] == "reject"
assert pipeline.gate({**keep, "availability": "private"}, cfg)[0] == "reject"

# --- adapter helpers (no network)
from fj.sources import fourchan, booru

assert fourchan._name("old jakarta soul", "SEA thread", "wsg", 123456789) == "old jakarta soul"
assert fourchan._name("1782260877440533", "SEA thread", "wsg", 1782260877440533).endswith("440533")
assert fourchan._name(None, "", "wsg", 42) == "/wsg/ 42"
assert booru._tag_str("a b  c") == "a, b, c"
assert booru._tag_str("a, b, c") == "a, b, c"
assert booru._title("e621.net", 7, "x") == "e621.net #7"

print("util/gate tests: ok")
