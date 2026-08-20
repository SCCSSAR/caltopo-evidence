"""
exif.py — minimal JPEG/EXIF inspection, standard library only.

WHY THIS EXISTS
    A CalTopo photo can carry a map coordinate that the CAMERA never recorded --
    someone placed it by hand. Measured on a real map: 2 of 44 located photos
    had a map coordinate and no EXIF GPS at all.

    Presenting both in one `latitude` column gives a human placement the same
    apparent authority as a camera fix. For an evidence record that distinction
    is the whole question: "the photo was taken here" and "someone later said it
    was taken here" are different claims.

    This module answers only "did the camera record a position?" -- it does not
    read the EXIF coordinate itself. CalTopo's own value is authoritative for
    the map; what we add is provenance, not a second opinion on the number.

SCOPE
    Deliberately small: enough JPEG marker walking to find an EXIF APP1 and look
    for a GPS IFD pointer. It is not a general EXIF library and should not grow
    into one -- a full parser is a large untrusted-input surface, and this tool
    reads files from the internet.
"""

from __future__ import annotations

import struct
from pathlib import Path

# Only the header is examined. EXIF lives at the front of a JPEG, so reading a
# few hundred KB avoids pulling 8 MB into memory per photo just to answer a
# yes/no question.
_HEADER_BYTES = 256 * 1024

_TAG_GPS_IFD = 0x8825

SOURCE_CAMERA = "camera (EXIF GPS)"
SOURCE_MAP_ONLY = "map only (no EXIF GPS)"
SOURCE_NONE = "none"
SOURCE_UNCHECKED = "not checked (image not downloaded)"


def _iter_segments(data: bytes):
    """Yield (marker, payload) for JPEG segments up to the start of scan."""
    if data[:2] != b"\xff\xd8":
        return
    i = 2
    while i < len(data) - 3:
        if data[i] != 0xFF:
            return
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:      # start of scan: nothing structured after this
            return
        try:
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
        except struct.error:
            return
        if length < 2:
            return
        yield marker, data[i + 4:i + 2 + length]
        i += 2 + length


def _ifd0_has_gps_pointer(payload: bytes) -> bool:
    if not payload.startswith(b"Exif\x00\x00"):
        return False
    tiff = payload[6:]
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        return False
    try:
        ifd0 = struct.unpack(endian + "I", tiff[4:8])[0]
        count = struct.unpack(endian + "H", tiff[ifd0:ifd0 + 2])[0]
    except (struct.error, IndexError):
        return False
    # A malformed count could otherwise drive a very long loop over garbage.
    for k in range(min(count, 512)):
        entry = ifd0 + 2 + k * 12
        try:
            tag = struct.unpack(endian + "H", tiff[entry:entry + 2])[0]
        except struct.error:
            return False
        if tag == _TAG_GPS_IFD:
            return True
    return False


def has_exif_gps(path: Path) -> bool:
    """
    True when the image carries an EXIF GPS IFD -- i.e. the camera recorded a
    position. Any parse failure returns False: an unreadable header is not
    evidence of a camera fix, and this must never report more certainty than
    the file supports.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEADER_BYTES)
    except OSError:
        return False
    for marker, payload in _iter_segments(head):
        if marker == 0xE1 and _ifd0_has_gps_pointer(payload):
            return True
    return False


def classify_coordinate_source(has_map_coord: bool, downloaded: bool,
                               path: Path | None) -> str:
    """
    Describe where a photo's coordinate came from.

    Without the image bytes we cannot tell, and we say so rather than defaulting
    to the flattering answer.
    """
    if not has_map_coord:
        return SOURCE_NONE
    if not downloaded or path is None or not path.exists():
        return SOURCE_UNCHECKED
    return SOURCE_CAMERA if has_exif_gps(path) else SOURCE_MAP_ONLY
