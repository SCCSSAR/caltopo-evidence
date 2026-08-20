"""
Guard: no real map data in the repository.

WHY THIS EXISTS
    This tool was built against live incident maps, so real map ids, real
    coordinates and real evidence-item names were present throughout during
    development. Scrubbing them before publication was a manual pass, and a
    manual pass is a plan file: it works once and then decays.

    The rule is written down in CONTRIBUTING.md. This makes it mechanical.

WHAT IT KEYS ON
    Precision, not value. 37.4 and 37.164325 are both plausible latitudes, but
    only the second carries roughly 10 cm of resolution -- nobody invents that
    by hand, so a coordinate-shaped literal with real digits past the third
    decimal place is a reliable fingerprint of pasted-in production data.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCANNED_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml"}
SKIPPED_DIRS = {".git", "bundles", "output", "build", "__pycache__", ".venv"}

# A coordinate-shaped number: optional sign, up to 3 integer digits, at least
# four decimal places.
_COORD = re.compile(r"-?\d{1,3}\.\d{4,}")

# A CalTopo map id is a short uppercase alphanumeric handle. So are a handful of
# legitimate technical terms, which is why this needs an allowlist rather than a
# tighter regex -- the shapes are genuinely identical.
# The surrounding-character guard excludes fragments of longer strings: without
# it, "2026-08-13T15:30" yields "13T15", which has the right shape and is not a
# map id.
_MAPID = re.compile(
    r"(?<![-:\w])"
    r"(?=[A-Z0-9]{5,8}(?![-:\w]))(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])"
    r"[A-Z0-9]{5,8}(?![-:\w])")
ALLOWED_MAPID_SHAPED = {
    "ABC123",    # the documented placeholder map id used throughout the docs
    "SHA256",    # the digest algorithm
    "APP14",     # the Adobe JPEG marker segment
    "ISO8601",   # date format, if it ever appears
    "BLE001",    # ruff lint code, suppressed inline on two broad excepts
}


def _scanned_files():
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if SKIPPED_DIRS.intersection(path.relative_to(REPO).parts):
            continue
        yield path


def _is_high_precision(token: str) -> bool:
    """True when the number carries real information past the third decimal.

    The CSV renders coordinates to 8 decimal places, so assertions on formatted
    output legitimately contain "37.10000000". Those are trailing zeros and
    carry no more information than 37.1 does.
    """
    decimals = token.split(".", 1)[1]
    return any(ch != "0" for ch in decimals[3:])


def _is_coordinate_range(token: str) -> bool:
    return -180.0 <= float(token) <= 180.0


class TestNoRealCoordinates(unittest.TestCase):
    def test_no_high_precision_coordinates_anywhere(self):
        offenders = []
        for path in _scanned_files():
            if path.name == Path(__file__).name:
                continue                        # this file documents the pattern
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                for token in _COORD.findall(line):
                    if _is_coordinate_range(token) and _is_high_precision(token):
                        offenders.append(f"{path.relative_to(REPO)}:{n}: {token}")
        self.assertEqual(offenders, [], "\n".join(
            ["High-precision coordinates found. Real incident coordinates must "
             "not be committed; use a low-precision placeholder such as 37.4. "
             "See CONTRIBUTING.md."] + offenders))


class TestNoMapIds(unittest.TestCase):
    def test_no_map_id_shaped_tokens_outside_the_allowlist(self):
        offenders = []
        for path in _scanned_files():
            if path.name == Path(__file__).name:
                continue
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                for token in _MAPID.findall(line):
                    if token not in ALLOWED_MAPID_SHAPED:
                        offenders.append(f"{path.relative_to(REPO)}:{n}: {token}")
        self.assertEqual(offenders, [], "\n".join(
            ["Map-id-shaped tokens found. A real CalTopo map id must never be "
             "committed; use the placeholder ABC123. If this is a legitimate "
             "technical term, add it to ALLOWED_MAPID_SHAPED with a comment."] + offenders))


if __name__ == "__main__":
    unittest.main()
