"""
extract.py — orchestration: map -> photos on disk -> CSV -> manifest.

OUTPUT LAYOUT
    <out>/<map_id>/
        <map_id>/                 photos (folder named for the map id)
        <map_id>_photos.csv       the report
        manifest.json             checksums + provenance
        manifest.sha256           digest of the manifest
        map_state.json            the raw payload the extraction was derived from

    map_state.json is kept deliberately. It is the source record: everything in
    the CSV is derived from it, so retaining it lets anyone re-derive the report
    later, or check what the map looked like at retrieval time after the live
    map has moved on. Its digest is in the manifest.

PII / CONSOLE
    Progress output is counts and CLASS names only. Photo titles, filenames,
    marker names and coordinates go to the CSV and the manifest -- never to
    stdout, which ends up pasted into tickets and transcripts.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .client import CalTopoError, CalTopoReadOnlyClient, sha256_file
from .exif import classify_coordinate_source
from .manifest import build_manifest, write_manifest
from . import model
from .model import CSV_COLUMNS, PhotoRecord


@dataclass
class ExtractionResult:
    out_dir: Path
    photo_dir: Path
    csv_path: Path
    manifest_path: Path
    records: list
    notes: list


class BundleExists(RuntimeError):
    """Refusing to write into a populated output directory."""


def _log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, flush=True)


def prepare_output_dir(out_root: Path, map_id: str, *, resume: bool = False,
                       force: bool = False) -> tuple[Path, Path]:
    """
    Create the bundle directory, refusing to silently overwrite an existing one.

    Write-once by default. An evidence bundle that can be quietly overwritten by
    a later run is one whose contents cannot be attributed to a particular
    retrieval -- and the overwrite would happen at exactly the moment someone is
    re-running because something looked wrong.

    --resume continues an interrupted run; --force replaces deliberately.
    """
    out_dir = out_root / map_id
    photo_dir = out_dir / map_id
    if out_dir.exists() and any(out_dir.iterdir()) and not (resume or force):
        raise BundleExists(
            f"{out_dir} already contains a bundle.\n"
            f"  --resume  continue an interrupted extraction (keeps verified files)\n"
            f"  --force   replace it deliberately\n"
            f"  or choose a different --out directory."
        )
    photo_dir.mkdir(parents=True, exist_ok=True)
    return out_dir, photo_dir


def extract(client: CalTopoReadOnlyClient, map_id: str, out_root: Path, *,
            limit: Optional[int] = None, resume: bool = False, force: bool = False,
            operator: Optional[str] = None, quiet: bool = False,
            progress: Optional[Callable[[int, int], None]] = None) -> ExtractionResult:
    started_at = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    notes: list = []

    out_dir, photo_dir = prepare_output_dir(out_root, map_id, resume=resume, force=force)

    # Announced only once the output directory is secured. Printing it before the
    # write-once guard made an aborted run look like a started one.
    if limit == 0:
        _log("Metadata-only run: reading the map and writing the CSV, downloading "
             "no image bytes.\n", quiet)

    # -- 1. map state (the source record) --------------------------------
    _log(f"Reading map {map_id} ...", quiet)
    map_state = client.get_map_state(map_id)
    state_path = out_dir / "map_state.json"
    state_text = json.dumps(map_state, indent=2, sort_keys=True, ensure_ascii=False)
    state_path.write_text(state_text, encoding="utf-8")
    map_state_sha = sha256_file(state_path)

    # -- 2. map identity --------------------------------------------------
    info = client.get_map_info(map_id)
    title = model.map_title(info)
    if not title:
        notes.append(
            "Map title unavailable: reading it requires CALTOPO_TEAM_ID and the "
            "account-scoped CollaborativeMap endpoint."
        )
        _log("  map title    : (unavailable: no team id configured)", quiet)
    else:
        _log(f"  map title    : {title}", quiet)

    by_id = model.index_features(map_state)
    media = model.media_features(map_state)
    _log(f"  features     : {len(by_id)}   photos: {len(media)}", quiet)

    # -- 3. build records --------------------------------------------------
    records: list[PhotoRecord] = []
    container_hist: dict = {}
    for feat in media:
        props = feat.get("properties") or {}
        ctype, cname = model.resolve_container(props.get("parentId"), by_id)
        container_hist[ctype] = container_hist.get(ctype, 0) + 1
        lat, lng = model.extract_coordinates(feat.get("geometry"))
        records.append(PhotoRecord(
            map_id=map_id,
            map_title=title,
            photo_title=props.get("title") or "",
            photo_description=props.get("description") or "",
            latitude=lat,
            longitude=lng,
            has_coordinates=lat is not None and lng is not None,
            container_type=ctype,
            container_name=cname,
            heading_deg=props.get("heading", ""),
            media_id=props.get("backendMediaId") or "",
            feature_id=feat.get("id") or "",
            download_status="pending",
        ))
    _log(f"  containers   : {container_hist}", quiet)
    no_coords = sum(1 for r in records if not r.has_coordinates)
    if no_coords:
        _log(f"  no coordinates on {no_coords} photo(s); reported, not inferred", quiet)

    # -- 4. per-photo metadata ---------------------------------------------
    _log(f"Fetching metadata for {len(records)} photo(s) ...", quiet)
    meta_failures = 0
    for r in records:
        if not r.media_id:
            r.download_status = "no backendMediaId"
            meta_failures += 1
            continue
        try:
            meta = (client.get_media_metadata(r.media_id) or {}).get("result") or {}
        except CalTopoError as e:
            r.download_status = f"metadata failed: {e}"
            meta_failures += 1
            continue
        mprops = meta.get("properties") or {}
        mmeta = meta.get("metadata") or {}
        r.caltopo_filename = mprops.get("filename") or ""
        r.exif_created_utc = model.epoch_ms_to_iso(mmeta.get("exifCreated"))
        r.exif_created_tz = mprops.get("exifCreatedTZ") or ""
        r.filesize_bytes = mmeta.get("filesize")
        r.extras["format"] = mmeta.get("format") or ""
    if meta_failures:
        notes.append(f"{meta_failures} photo(s) had no retrievable metadata.")
    _log(f"  metadata ok  : {len(records) - meta_failures}/{len(records)}", quiet)

    # -- 5. download ---------------------------------------------------------
    targets = [r for r in records if r.download_status == "pending"]
    if limit is not None:
        targets = targets[:max(0, limit)]
        if len(targets) < len([r for r in records if r.download_status == "pending"]):
            # The note must describe what the operator did, not the internal
            # value it became: a default metadata-only run never typed "--limit 0"
            # and a manifest that says otherwise misdescribes the retrieval.
            notes.append(
                f"METADATA-ONLY BUNDLE: no image bytes were downloaded; the CSV "
                f"describes all {len(records)} photos. Re-run with --all to fetch them."
                if limit == 0 else
                f"PARTIAL BUNDLE: --limit {limit} was used; only {len(targets)} of "
                f"{len(records)} photos were downloaded."
            )
    est = sum(r.filesize_bytes or 0 for r in targets)
    _log(f"Downloading {len(targets)} of {len(records)} original(s)"
         + (f", about {est / (1024*1024):.0f} MB ..." if est else " ..."), quiet)

    used_names: set = set()
    # Seed collision state from disk so a resumed run cannot re-issue a name.
    if resume and photo_dir.exists():
        used_names.update(p.name.lower() for p in photo_dir.iterdir() if p.is_file())

    resumed = 0
    for i, r in enumerate(targets, start=1):
        base = model.safe_filename(
            r.caltopo_filename or r.photo_title,
            fallback=(r.media_id or r.feature_id or "photo")[:12],
            default_ext=r.extras.get("format") or "jpg",
        )
        name = model.dedupe_filename(base, used_names)
        dest = photo_dir / name

        if resume and dest.exists() and r.filesize_bytes and dest.stat().st_size == r.filesize_bytes:
            # Size match is the cheap gate; the digest is still computed so the
            # manifest records what is actually on disk, not what we assumed.
            r.saved_filename = name
            r.sha256 = sha256_file(dest)
            r.download_status = "ok (resumed)"
            resumed += 1
            if progress:
                progress(i, len(targets))
            continue

        try:
            written, sha = client.download_media(r.media_id, dest,
                                                 expected_bytes=r.filesize_bytes)
        except CalTopoError as e:
            r.download_status = f"failed: {e}"
            used_names.discard(name.lower())
            if progress:
                progress(i, len(targets))
            continue
        r.saved_filename = name
        r.sha256 = sha
        if r.filesize_bytes is None:
            r.filesize_bytes = written
        r.download_status = "ok"
        if progress:
            progress(i, len(targets))

    # Coordinate provenance. Runs after download because it needs the bytes:
    # a map coordinate with no EXIF GPS was placed by a person, not recorded by
    # a camera, and an evidence CSV must not present the two identically.
    # Two independent positions per row, never merged.
    #
    # `latitude`/`longitude` are the PHOTO's own -- what the camera or the person
    # placing the photo recorded. `marker_latitude`/`marker_longitude` are the
    # container's. A responder can drop a marker and walk before shooting, so
    # collapsing the two would assert a precision the data does not carry.
    # Unattached photos get N/A for the marker pair: the question does not apply.
    container_without_coords = 0
    for r in records:
        if r.container_type != model.UNATTACHED:
            feat = by_id.get(r.feature_id) or {}
            mlat, mlng = model.container_coordinates(
                (feat.get("properties") or {}).get("parentId"), by_id)
            r.marker_latitude, r.marker_longitude = mlat, mlng
            if mlat is None:
                container_without_coords += 1
        downloaded = r.download_status.startswith("ok") and bool(r.saved_filename)
        r.coordinate_source = classify_coordinate_source(
            r.has_coordinates, downloaded,
            photo_dir / r.saved_filename if r.saved_filename else None)

    only_marker = sum(1 for r in records
                      if not r.has_coordinates and r.marker_latitude is not None)
    if only_marker:
        notes.append(
            f"{only_marker} photo(s) have no coordinate of their own but are attached "
            f"to a located marker; see the marker_latitude/marker_longitude columns."
        )
    if container_without_coords:
        notes.append(
            f"{container_without_coords} photo(s) are attached to a container that "
            f"carries no coordinate; their marker columns read N/A."
        )

    ok = sum(1 for r in records if r.download_status.startswith("ok"))
    failed = [r for r in records if r.download_status.startswith("failed")]
    _log(f"  downloaded   : {ok}/{len(targets)}"
         + (f"  (resumed {resumed})" if resumed else ""), quiet)
    if failed:
        notes.append(f"{len(failed)} download(s) failed.")
        _log(f"  FAILURES     : {len(failed)}; see download_status in the CSV", quiet)

    # -- 6. CSV --------------------------------------------------------------
    csv_name = f"{map_id}_photos.csv"
    csv_path = out_dir / csv_name
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in records:
            writer.writerow(r.as_csv_row())
    csv_sha = sha256_file(csv_path)

    # -- 7. manifest ----------------------------------------------------------
    manifest = build_manifest(
        map_id=map_id, map_title=title, base_url=client.base_url,
        records=records, map_state_sha256=map_state_sha,
        csv_name=csv_name, csv_sha256=csv_sha, photo_dir_name=photo_dir.name,
        operator=operator, started_at=started_at, notes=notes,
    )
    # map_state.json is part of the bundle, so it belongs in the file list too.
    manifest["files"].append({
        "saved_filename": state_path.name,
        "relative_path": state_path.name,
        "sha256": map_state_sha,
        "bytes": state_path.stat().st_size,
        "caltopo_filename": "",
        "media_id": "",
        "feature_id": "",
        "container_type": "(source record)",
        "has_coordinates": False,
    })
    manifest_path, digest_path = write_manifest(out_dir, manifest)

    _log("", quiet)
    _log(f"  CSV          : {csv_path.name}  ({len(records)} rows)", quiet)
    _log(f"  manifest     : {manifest_path.name} + {digest_path.name}", quiet)
    _log(f"  bundle       : {out_dir}", quiet)
    _log("  Bundle contents are not printed; they may be incident evidence.", quiet)

    return ExtractionResult(out_dir=out_dir, photo_dir=photo_dir, csv_path=csv_path,
                            manifest_path=manifest_path, records=records, notes=notes)
