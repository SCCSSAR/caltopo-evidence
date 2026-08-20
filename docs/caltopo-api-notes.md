# CalTopo read API — measured notes

CalTopo's read API is thinly documented. Everything here was established empirically
against live maps in August 2026, while building this tool. It is recorded so nobody has
to re-walk it.

This is an observed contract, not a published one. CalTopo is free to change any of it
without notice, and nothing here should be read as CalTopo endorsing or supporting this
use. Re-measure before trusting it.

Learned empirically against a live map; CalTopo's read API is thinly documented.

| Purpose | Endpoint |
|---|---|
| Map state | `GET /api/v1/map/{mapId}/since/-1` |
| Map title / mode / sharing | `GET /api/v1/acct/{teamId}/CollaborativeMap/{mapId}` |
| Photo metadata | `GET /api/v1/media/{backendMediaId}` |
| Photo bytes | `GET /api/v1/media/{backendMediaId}/original` |

Requests are signed HMAC-SHA256 over `"GET {path}\n{expires}\n"` — path only, not the
query string that carries the auth parameters.

Things that cost time to discover, recorded so nobody re-walks them for future development purposes:

- **`parentId` is `"{ClassName}:{uuid}"`, not a bare feature id.** Split on the first
  colon. `Marker` and `Folder` are both exactly six characters, so a fixed-width prefix
  strip conflates them silently.
- **A photo with no `parentId` is unattached**, which is CalTopo's default. Uploading
  into a selected folder in the web UI still files the photo under *Unattached Photos* —
  a folder is not a photo destination there.
- **`GET /api/v1/map/{mapId}/media/{backendMediaId}` returns HTTP 200 with a zero-byte
  body.** It looks like success. Treat an empty body as failure.
- **`/api/v1/media/{id}.jpg` returns 400.** The extension goes nowhere; `/original` is
  the verb.
- **`exifCreated` is epoch milliseconds** (13 digits).
- **Media geometries carry four coordinate elements**; the third and fourth were `0`
  across an entire map and are not elevation data.
- **CalTopo filenames do not always carry an extension.** Fall back to the metadata
  `format` field.
- **CalTopo re-encodes uploaded photos** — see *What CalTopo actually stores*, above.
  Surface inspection suggests otherwise and is misleading: across a 48-photo map every
  file retained an EXIF APP1 block with intact camera make and model (nine distinct
  devices), 42 retained a GPS IFD, 35 retained an ICC profile, and **none** carried an
  Adobe APP14 marker. That pattern reads like untouched originals. Comparing against
  retained capture files settled it the other way. Do not infer provenance from marker
  survival alone.
- **No photo had EXIF GPS without also having a map coordinate**, so CalTopo does consume
  the camera fix when one exists. The reverse happens: a map coordinate with no EXIF GPS
  means a human placed it.
- **In a HEIC, searching for `Exif\x00\x00` finds the item *declaration*, not the
  payload.** Seek the TIFF magic instead. The naive search returns zero tags, which reads
  identically to "this file has no EXIF" — a parser failure wearing the costume of a
  finding. (This tool only reads JPEG, but the trap bit during the capture-file
  comparison and is worth recording.)

## Download paths that do not work

Ten plausible ways to fetch photo bytes were probed. The ones worth naming:

| Attempt | Result |
|---|---|
| `GET /api/v1/map/{mapId}/media/{mediaId}` | **HTTP 200 with a zero-byte body** — looks like success |
| `GET /api/v1/media/{mediaId}.jpg` | HTTP 400; the extension goes nowhere |
| `GET /api/v1/media/{mediaId}/original` | ✅ the working path |

The zero-byte 200 is the dangerous one. A client that checks only the status code will
write empty files and report every photo as retrieved. `client.get_json()` treats an
empty 200 as a failure for exactly this reason.

## Request signing

```
signing string:  "GET {path}\n{expires_ms}\n"
signature:       base64( HMAC-SHA256( base64_decode(secret), signing_string ) )
transport:       id / expires / signature as QUERY PARAMETERS on GET
```

The signature covers the **path only** — not the query string that carries the auth
parameters. The trailing newline and the empty payload segment are both load-bearing.
POST requests pass the same three values as form fields instead, which is why a client
that only ever GETs can keep the signing code this small.
