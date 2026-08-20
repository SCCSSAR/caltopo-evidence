"""
model.py — pure logic: container resolution, coordinates, filenames, CSV rows.

Everything here is deterministic and network-free so it can be unit tested
directly. The network-touching code lives in client.py and the orchestration in
extract.py; keeping this separable is what makes the rules below testable rather
than merely asserted.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Optional

MEDIA_CLASS = "MapMediaObject"
UNATTACHED = "Unattached"

# Printed in the marker coordinate columns when there is no container position to
# report. An explicit "N/A" beats a blank cell: a blank reads as a data gap the
# tool failed to fill, where N/A says the question does not apply to this row.
NOT_APPLICABLE = "N/A"


def _coord_or_na(value):
    return NOT_APPLICABLE if value is None else f"{value:.8f}"

# CSV column order. The map identity leads every row so a row remains
# self-describing after someone sorts, filters, or pastes it somewhere else --
# an evidence row that cannot say which map it came from is not evidence.
CSV_COLUMNS = [
    "map_id",
    "map_title",
    "saved_filename",
    "caltopo_filename",
    "photo_title",
    "photo_description",
    "latitude",
    "longitude",
    "has_coordinates",
    "coordinate_source",
    "marker_latitude",
    "marker_longitude",
    "container_type",
    "container_name",
    "heading_deg",
    "exif_created_utc",
    "exif_created_tz",
    "filesize_bytes",
    "sha256",
    "media_id",
    "feature_id",
    "download_status",
]


@dataclass
class PhotoRecord:
    """One photo, as it will appear in the CSV and the manifest."""
    map_id: str = ""
    map_title: str = ""
    saved_filename: str = ""
    caltopo_filename: str = ""
    photo_title: str = ""
    photo_description: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    has_coordinates: bool = False
    coordinate_source: str = ""
    marker_latitude: object = None
    marker_longitude: object = None
    container_type: str = UNATTACHED
    container_name: str = ""
    heading_deg: Any = ""
    exif_created_utc: str = ""
    exif_created_tz: str = ""
    filesize_bytes: Optional[int] = None
    sha256: str = ""
    media_id: str = ""
    feature_id: str = ""
    download_status: str = "pending"
    extras: dict = field(default_factory=dict)

    def as_csv_row(self) -> dict:
        return {
            "map_id": self.map_id,
            "map_title": self.map_title,
            "saved_filename": self.saved_filename,
            "caltopo_filename": self.caltopo_filename,
            "photo_title": self.photo_title,
            "photo_description": self.photo_description,
            "latitude": "" if self.latitude is None else f"{self.latitude:.8f}",
            "longitude": "" if self.longitude is None else f"{self.longitude:.8f}",
            "has_coordinates": "yes" if self.has_coordinates else "no",
            "coordinate_source": self.coordinate_source,
            "marker_latitude": _coord_or_na(self.marker_latitude),
            "marker_longitude": _coord_or_na(self.marker_longitude),
            "container_type": self.container_type,
            "container_name": self.container_name,
            "heading_deg": "" if self.heading_deg in (None, "") else self.heading_deg,
            "exif_created_utc": self.exif_created_utc,
            "exif_created_tz": self.exif_created_tz,
            "filesize_bytes": "" if self.filesize_bytes is None else self.filesize_bytes,
            "sha256": self.sha256,
            "media_id": self.media_id,
            "feature_id": self.feature_id,
            "download_status": self.download_status,
        }


def resolve_container(parent_id: Optional[str], by_id: dict) -> tuple[str, str]:
    """
    Map a MapMediaObject `parentId` to (container_type, container_name).

    A `parentId` is "{ClassName}:{featureId}" -- NOT a bare feature id. An
    earlier revision compared the whole prefixed string against bare ids,
    matched nothing, and concluded the map's photo links were corrupt. They
    were not; the key was wrong.

    Two rules follow, and both are load-bearing:

    1. Split on the FIRST colon only. "Marker" and "Folder" are both exactly six
       characters, so any fixed-width prefix strip conflates them, and the id
       half is opaque -- we do not get to assume it never contains a colon.

    2. No parentId means Unattached. That is CalTopo's default and it is where a
       photo lands even when a folder is selected in the UI; a folder is not a
       photo destination there. Verified behaviorally 2026-08-19.

    When the prefix names a class whose target is missing from the map, the
    declared class is still reported -- "a Marker we cannot find" is more useful
    to a human reading the CSV than a blank cell.
    """
    if not parent_id:
        return UNATTACHED, ""
    declared, sep, feature_id = parent_id.partition(":")
    if not sep:
        declared, feature_id = "", parent_id
    feature = by_id.get(feature_id)
    if feature is None:
        return (declared or "Unknown"), "(container not found on map)"
    props = feature.get("properties") or {}
    actual = props.get("class") or declared or "Unknown"
    name = props.get("title") or props.get("name") or ""
    return actual, name


def container_coordinates(parent_id, by_id) -> tuple:
    """
    The coordinate of a photo's container, when the photo has none of its own.

    Measured on a real map: 4 of 48 photos carried no geometry, and every one of
    them was attached to a marker that DID have coordinates. Reporting those as
    blank discards a location the map plainly shows.

    The caller must label the result as INHERITED. It is the container's
    position, not the photo's -- a responder can drop a marker and then walk
    some distance before shooting -- so it answers "roughly where was this
    photo taken" and must never be presented as a camera fix.
    """
    if not parent_id:
        return None, None
    _, sep, feature_id = parent_id.partition(":")
    if not sep:
        feature_id = parent_id
    feature = by_id.get(feature_id)
    if feature is None:
        return None, None
    return extract_coordinates(feature.get("geometry"))


def extract_coordinates(geometry: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    """
    Pull (latitude, longitude) out of a GeoJSON geometry.

    GeoJSON orders coordinates lon,lat -- the reverse of how every SAR-facing
    surface writes them, which is why this is a named function rather than an
    inline index. CalTopo media geometries carry four elements; elements 3 and 4
    were measured as 0 across a whole map and are not elevation data.

    Returns (None, None) for anything that is not a usable numeric pair. Some
    photos genuinely carry no location and that is reported, never inferred.
    """
    if not isinstance(geometry, dict):
        return None, None
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None, None
    lng, lat = coords[0], coords[1]
    if isinstance(lng, bool) or isinstance(lat, bool):
        return None, None
    if not isinstance(lng, (int, float)) or not isinstance(lat, (int, float)):
        return None, None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return None, None
    return float(lat), float(lng)


def epoch_ms_to_iso(value: Any) -> str:
    """
    CalTopo `exifCreated` is epoch MILLISECONDS (13 digits, measured).

    Returns "" for anything unparseable rather than guessing. A timestamp on an
    evidence record is exactly the kind of field that must not be invented.
    """
    if value in (None, ""):
        return ""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    try:
        dt = _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


_ILLEGAL = set('<>:"|?*\\/')


def safe_filename(preferred: str, fallback: str, default_ext: str = "jpg") -> str:
    """
    Build a filesystem-safe filename that always carries an extension.

    The extension fallback applies on EVERY path. An earlier revision computed
    it correctly and then used it only on the collision branch, so ordinary
    downloads wrote extensionless files -- while byte counts matched, the CSV
    was complete, and every status field read "ok". The defect was visible only
    in a directory listing, which is precisely why it is called out here.
    """
    name = (preferred or "").strip()
    name = "".join(c for c in name if c.isprintable() and c not in _ILLEGAL)
    name = name.strip(". ")
    if not name:
        name = fallback
    stem, dot, ext = name.rpartition(".")
    # Cap at 4 characters: every image extension we care about fits (jpg, jpeg,
    # tiff, heic, webp, png), while a trailing English word in a descriptive
    # title does not. "item.found.near.trail" must not be filed as a .trail.
    if not dot or not ext or len(ext) > 4 or not ext.isalnum():
        stem, ext = name, default_ext
    stem = stem.strip(". ") or fallback
    return f"{stem}.{ext.lower()}"


def dedupe_filename(candidate: str, used: set) -> str:
    """
    Make `candidate` unique against `used`, case-insensitively.

    Case matters: macOS and Windows filesystems are case-insensitive, so
    "Item.jpg" and "item.jpg" collide there and silently overwrite -- losing a
    photo from the bundle while every status still reads ok. This is not
    hypothetical: real maps carry photo titles differing only by case.
    """
    if candidate.lower() not in used:
        used.add(candidate.lower())
        return candidate
    stem, _, ext = candidate.rpartition(".")
    n = 2
    while f"{stem}_{n}.{ext}".lower() in used:
        n += 1
    result = f"{stem}_{n}.{ext}"
    used.add(result.lower())
    return result


def media_features(map_state: dict) -> list:
    """Every MapMediaObject in a /since/-1 payload, in payload order."""
    features = (((map_state or {}).get("result") or {}).get("state") or {}).get("features") or []
    return [f for f in features
            if isinstance(f, dict) and (f.get("properties") or {}).get("class") == MEDIA_CLASS]


def index_features(map_state: dict) -> dict:
    features = (((map_state or {}).get("result") or {}).get("state") or {}).get("features") or []
    return {f.get("id"): f for f in features if isinstance(f, dict) and f.get("id")}


def map_title(map_info: dict) -> str:
    """Human-readable map name from the account-scoped CollaborativeMap object."""
    return (((map_info or {}).get("result") or {}).get("properties") or {}).get("title") or ""
