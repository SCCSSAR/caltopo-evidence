# caltopo-evidence

Read-only extraction of photos and their metadata from a CalTopo map, producing a
checksummed bundle: the original-resolution images, a CSV report, and an integrity
manifest recording where every byte came from.

Built for Santa Clara County Sheriff's Search and Rescue to pull photo evidence out of
incident maps without hand-downloading each image and hand-typing its coordinates.

**Standard library only.** No third-party dependencies. Python 3.9+.

---

## What it does

Given a CalTopo map id, for every photo on the map:

- downloads the **original-resolution** image (not a thumbnail)
- records its **name**, **latitude/longitude**, and **compass heading**
- resolves what it is **attached to** — a marker, a folder, an assignment, or nothing
- computes a **SHA-256** of every file as it is written
- writes a **CSV report** and a **manifest** you can re-verify later

## Usage

```bash
export CALTOPO_CREDENTIAL_ID=...
export CALTOPO_CREDENTIAL_SECRET=...      # base64, as CalTopo issues it
export CALTOPO_TEAM_ID=...                # optional; needed for the map title

python3 -m caltopo_evidence extract ABC123
python3 -m caltopo_evidence verify bundles/ABC123
```

Credentials in Google Secret Manager instead? **The flag goes after the subcommand:**

```bash
python3 -m caltopo_evidence extract ABC123 --from-gcloud my-gcp-project
```

It reads `caltopo-credential-id`, `caltopo-credential-secret` and `caltopo-team-id` from
that project using whatever account `gcloud` is currently authenticated as. If the token
has expired, `gcloud auth login <account>` first — `gcloud config set account` alone does
not refresh it.

**Extraction downloads the photos.** That is the default, because it is what the command
says it does. The run announces the size up front (a 48-photo map is roughly 350 MB) so
the operator knows what is coming.

For a fast look at what a map holds without pulling the images:

```bash
python3 -m caltopo_evidence extract ABC123 --metadata-only    # CSV only, no bytes
python3 -m caltopo_evidence extract ABC123 --limit 5          # a sample
```

Bundles are write-once: re-running into an existing bundle needs `--force` (replace) or
`--resume` (continue an interrupted run). Note that `--force` only governs *replacing the
directory* — it has never had anything to do with whether photos are downloaded.

Operators keeping credentials in Google Secret Manager can use
`--from-gcloud <project>` instead of exporting variables. That path is opt-in so the
default carries no cloud-provider assumption.

## Output

```
bundles/<map_id>/
├── <map_id>/                  the photos
├── <map_id>_photos.csv        the report
├── map_state.json             the raw map payload the report was derived from
├── manifest.json              checksums + provenance
└── manifest.sha256            digest of the manifest
```

`map_state.json` is kept on purpose. Everything in the CSV is derived from it, so
retaining it means the report can be re-derived later, and it preserves what the map
looked like at retrieval time after the live map has moved on.

### CSV columns

`map_id`, `map_title`, `saved_filename`, `caltopo_filename`, `photo_title`,
`photo_description`, `latitude`, `longitude`, `has_coordinates`, `coordinate_source`,
`marker_latitude`, `marker_longitude`, `container_type`, `container_name`, `heading_deg`,
`exif_created_utc`, `exif_created_tz`, `filesize_bytes`, `sha256`, `media_id`,
`feature_id`, `download_status`

Every photo gets a row, including photos with no location — those carry
`has_coordinates=no` and empty coordinate cells. Nothing is dropped silently, and a
missing coordinate is never inferred.

### Two positions per row, never merged

`latitude` / `longitude` are the **photo's own** position. `marker_latitude` /
`marker_longitude` are the position of the **marker it is attached to**, or `N/A` when it
is attached to nothing.

They are reported separately because they are different facts. A responder can drop a
marker and then walk before taking the photo — on the map used for validation, a photo
sat about 1.6 m from the marker it was attached to. Collapsing them into one column would
assert a precision the data does not carry.

This also means a photo with no camera fix still has a usable position: during testing,
4 photos carried no coordinate of their own but were attached to located markers. Their
`latitude` cells stay **empty** — the marker position is never promoted into the photo's
column — while `marker_latitude` gives the answer a reader actually needs.

### `coordinate_source` — where the photo's own position came from

A CalTopo photo can carry a map coordinate the **camera never recorded**: someone placed
it by hand. Measured on a real map during testing, 2 of 44 located photos were positioned this way.

| Value | Meaning |
|---|---|
| `camera (EXIF GPS)` | the image carries an EXIF GPS record — the camera fixed the position |
| `map only (no EXIF GPS)` | a person set this position on the map |
| `none` | no coordinate at all |
| `not checked (image not downloaded)` | metadata-only run; the bytes were never fetched |

Presenting both in a single `latitude` column would give a human placement the same
apparent authority as a camera fix. "The photo was taken here" and "someone later said
it was taken here" are different claims, and only one of them is a measurement.

## Integrity — what the manifest does and does not prove

`manifest.json` records a SHA-256 for every artifact plus the provenance of the
retrieval: when, from which map, via which endpoints, by which operator, and how many
bytes CalTopo declared versus how many arrived. `manifest.sha256` holds the digest of
the manifest itself.

**This is tamper-evidence, not authentication.** There is no signing key. It proves a
bundle has not changed since the manifest was written; it does not prove who wrote the
manifest, and anyone able to rewrite the bundle can rewrite both files. Custody of the
sidecar digest — recorded or transmitted separately — is what gives it force. Do not
describe it as a signature in a declaration.

`verify` checks the manifest against its sidecar, the CSV, every listed file's digest
and size, **and the directory for files the manifest does not list**. That last check
matters: a bundle with an *extra* file is as compromised as one with a modified file,
and a per-file loop alone will never notice it.

Bundles are **write-once**. Running `extract` into a populated directory refuses unless
you pass `--resume` or `--force`.

## What CalTopo actually stores — read this before relying on a checksum

**CalTopo does not hold the capture file.** Measured 2026-08-19 against two photos whose
originals were retained on the capturing phone:

- The camera captured **HEIC**. CalTopo serves **JPEG**. Something in the upload path
  transcodes.
- Byte-for-byte equality between a transcoded capture file and its CalTopo copy is therefore
  **impossible by construction**. A checksum mismatch between the two is expected and is
  not evidence of tampering.
- Pixel data is re-encoded: quantization tables differ per image, JFIF and ICC segments
  are dropped, and the EXIF block shrinks from ~10 KB to ~3 KB as the maker note is
  rewritten.

**The metadata survives intact.** Comparing the capture HEIC against the JPEG CalTopo
serves, every evidence-relevant EXIF tag is byte-identical: `DateTimeOriginal`,
`SubSecTimeOriginal`, `GPSLatitude`, `GPSLongitude`, `GPSAltitude`, camera `Model`,
`ExposureTime`, `ISO`. Only the maker note is rewritten (and `FocalLength` differed on
one of the two — not yet explained).

### What this means for evidence

The photo in a CalTopo map is a **derivative**, not an original. The original exists only
on the capturing device.

- The SHA-256 in this tool's manifest proves the bundle matches **what CalTopo served**.
  It does not and cannot prove the bundle matches what the camera produced.
- **When the images themselves may be contested, the capture device is the evidence.**
  Preserve the phone or export unmodified originals from it; the CalTopo copy is a
  working reference.
- **When location and timing are what matter, the CalTopo copy is trustworthy** — those
  fields pass through the transcode unchanged.

## Notes on CalTopo's data model

CalTopo's read API is thinly documented. The endpoints this tool uses, the request
signing scheme, and the behaviours that cost time to discover are written up in
[docs/caltopo-api-notes.md](docs/caltopo-api-notes.md).

## Tests

```bash
python3 -m unittest discover -s tests
```

63 tests, no network and no credentials required. The suite has been mutation-tested:
23 deliberate defects were introduced into the production modules — wrong `parentId`
parsing, swapped lat/lng, milliseconds read as seconds, the extension fallback regressed,
marker coordinates promoted into the photo's column, each integrity check disabled in
turn, coordinate provenance over-claimed — and all 23 were caught.

Mutation testing is the standard here because a passing test proves nothing on its own;
what matters is that the test **fails when the code breaks**. One check did leak on the
first pass — the manifest's file-size comparison, which turned out to be redundant with
the digest check for detection. Rather than inflate it, the test now pins what it
actually delivers: a truncated file reports *truncation*, not a generic digest mismatch.

## Status

Pre-release. Validated end to end against two real incident maps. The larger run was
48 photos and 350 MB in ~44 seconds, with the write-once guard, resume, tamper detection
and verification all exercised against live data.

Not yet exercised against `Folder` or `Assignment` containers with real data — the code
path is class-agnostic and unit tested, but has not met the real thing.
