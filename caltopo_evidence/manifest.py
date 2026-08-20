"""
manifest.py — integrity manifest for an extracted evidence bundle.

WHAT THIS DOES AND DOES NOT PROVIDE
    The manifest records a SHA-256 for every artifact in the bundle, plus the
    provenance of the retrieval: when, from which map, via which endpoints, by
    which operator, and how many bytes CalTopo declared versus how many arrived.
    A sidecar file holds the digest of the manifest itself, so tampering is
    detectable when the sidecar is recorded or communicated separately.

    This is TAMPER-EVIDENCE, not authentication. There is no signing key, so it
    proves that a bundle has not changed since the manifest was written -- it
    does not prove who wrote the manifest, and anyone who can rewrite the bundle
    can rewrite both files. Do not describe it in a declaration as a signature.
    Custody of the sidecar digest is what gives it force.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import hashlib
import json
import platform
import socket
from pathlib import Path
from typing import Optional

from . import TOOL_NAME, __version__
from .client import PATH_MAP_INFO, PATH_MAP_STATE, PATH_MEDIA_BYTES, PATH_MEDIA_META, sha256_file

MANIFEST_NAME = "manifest.json"
MANIFEST_DIGEST_NAME = "manifest.sha256"
MANIFEST_SCHEMA = 1


def _utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _operator(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:                                         # noqa: BLE001
        return "unknown"


def build_manifest(*, map_id: str, map_title: str, base_url: str,
                   records: list, map_state_sha256: str, csv_name: str,
                   csv_sha256: str, photo_dir_name: str,
                   operator: Optional[str] = None,
                   started_at: Optional[str] = None,
                   notes: Optional[list] = None) -> dict:
    """Assemble the manifest document. Pure -- does no I/O."""
    files = []
    for r in records:
        if r.download_status != "ok" or not r.saved_filename:
            continue
        files.append({
            "saved_filename": r.saved_filename,
            "relative_path": f"{photo_dir_name}/{r.saved_filename}",
            "sha256": r.sha256,
            "bytes": r.filesize_bytes,
            "caltopo_filename": r.caltopo_filename,
            "media_id": r.media_id,
            "feature_id": r.feature_id,
            "container_type": r.container_type,
            "has_coordinates": r.has_coordinates,
        })

    statuses = {}
    for r in records:
        statuses[r.download_status] = statuses.get(r.download_status, 0) + 1

    return {
        "manifest_schema": MANIFEST_SCHEMA,
        "integrity_note": (
            "SHA-256 digests provide tamper-evidence only. This manifest is not "
            "cryptographically signed and does not authenticate its author."
        ),
        "tool": {"name": TOOL_NAME, "version": __version__,
                 "python": platform.python_version(), "platform": platform.platform()},
        "retrieval": {
            "started_at_utc": started_at or _utc_now(),
            "completed_at_utc": _utc_now(),
            "operator": _operator(operator),
            "source_base_url": base_url,
            "endpoints_used": {
                "map_state": PATH_MAP_STATE,
                "map_info": PATH_MAP_INFO,
                "media_metadata": PATH_MEDIA_META,
                "media_bytes": PATH_MEDIA_BYTES,
            },
        },
        "source_map": {"map_id": map_id, "map_title": map_title,
                       "map_state_sha256": map_state_sha256},
        "counts": {
            "photos_found": len(records),
            "files_downloaded": len(files),
            "total_bytes": sum(f["bytes"] or 0 for f in files),
            "by_status": statuses,
            "without_coordinates": sum(1 for r in records if not r.has_coordinates),
        },
        "report": {"csv_filename": csv_name, "csv_sha256": csv_sha256},
        "files": files,
        "notes": notes or [],
    }


def write_manifest(out_dir: Path, manifest: dict) -> tuple[Path, Path]:
    """
    Write manifest.json and its digest sidecar.

    The manifest cannot contain its own digest, so the sidecar carries it. Keep
    or transmit the sidecar separately from the bundle for it to mean anything.
    """
    mpath = out_dir / MANIFEST_NAME
    # sort_keys + fixed separators: the serialization must be reproducible or
    # the sidecar digest is not checkable by anyone re-serializing the document.
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
    mpath.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    dpath = out_dir / MANIFEST_DIGEST_NAME
    dpath.write_text(f"{digest}  {MANIFEST_NAME}\n", encoding="utf-8")
    return mpath, dpath


def verify_bundle(out_dir: Path) -> dict:
    """
    Re-verify an extracted bundle against its manifest.

    Checks, in order: the manifest against its sidecar digest; the CSV; every
    listed file's digest and size; and finally the directory for files that the
    manifest does not list. That last check is the one people forget -- a bundle
    with an EXTRA file is just as compromised as one with a modified file, and
    a per-file loop alone will never notice it.
    """
    result = {"ok": True, "checked": 0, "problems": []}

    def fail(msg):
        result["ok"] = False
        result["problems"].append(msg)

    mpath = out_dir / MANIFEST_NAME
    if not mpath.exists():
        fail(f"missing {MANIFEST_NAME}")
        return result

    text = mpath.read_text(encoding="utf-8")
    actual_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    dpath = out_dir / MANIFEST_DIGEST_NAME
    if not dpath.exists():
        fail(f"missing {MANIFEST_DIGEST_NAME} — manifest integrity cannot be checked")
    else:
        recorded = dpath.read_text(encoding="utf-8").split()[0].strip()
        if recorded != actual_digest:
            fail("manifest.json does not match manifest.sha256 — manifest was modified")

    manifest = json.loads(text)

    csv_name = (manifest.get("report") or {}).get("csv_filename") or ""
    csv_path = out_dir / csv_name if csv_name else None
    if csv_path and csv_path.exists():
        if sha256_file(csv_path) != (manifest.get("report") or {}).get("csv_sha256"):
            fail(f"{csv_name}: digest mismatch")
        result["checked"] += 1
    elif csv_name:
        fail(f"{csv_name}: missing")

    listed = set()
    for entry in manifest.get("files", []):
        rel = entry.get("relative_path") or entry.get("saved_filename")
        listed.add(rel)
        fpath = out_dir / rel
        if not fpath.exists():
            fail(f"{rel}: missing")
            continue
        # Size is checked before the digest purely for DIAGNOSTICS. It is
        # redundant for detection -- any size change also changes the digest --
        # but "size 3 != recorded 203" tells a reader the file was truncated,
        # where a bare digest mismatch would suggest the contents were altered.
        # Those are different stories to tell about an evidence bundle.
        size = fpath.stat().st_size
        if entry.get("bytes") is not None and size != entry["bytes"]:
            fail(f"{rel}: size {size} != recorded {entry['bytes']}")
            continue
        if sha256_file(fpath) != entry.get("sha256"):
            fail(f"{rel}: digest mismatch")
            continue
        result["checked"] += 1

    photo_dirs = {rel.split("/", 1)[0] for rel in listed if "/" in rel}
    for d in photo_dirs:
        p = out_dir / d
        if not p.is_dir():
            continue
        for f in p.iterdir():
            if f.is_file() and f"{d}/{f.name}" not in listed:
                fail(f"{d}/{f.name}: present in bundle but not listed in manifest")

    return result
