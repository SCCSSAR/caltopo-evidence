# Security

**Disclosure policy:** Report security issues through [GitHub private vulnerability reporting](https://github.com/SCCSSAR/caltopo-evidence/security/advisories/new), which keeps the report private until there is a fix. If that is not available to you, email [bill.burns@sccssar.org](mailto:bill.burns@sccssar.org).

**Security update policy:** Reports are acknowledged within 72 hours. Fixes ship in a timeframe matched to severity.

## What this tool touches

It issues signed HTTP GET requests to CalTopo, writes files to a local directory, and reads the first 256 KB of each downloaded image to check for an EXIF GPS record. That is the whole surface.

There is no POST, PUT, or DELETE code path anywhere in it. The maps it reads are operational SAR records and sometimes evidence, so an accidental write would be unrecoverable and would also damage the integrity of the record. Read-only is a structural property here, not a setting.

## Security considerations

**Your CalTopo credentials are team-wide.** A CalTopo Team API credential is not scoped to a single map or to read-only access. This tool only reads, but the credential you hand it can do more than that. Treat it accordingly: keep it in environment variables or a secret manager, never in a file you might commit, and rotate it if it is ever exposed.

**The `--from-gcloud` path uses whatever `gcloud` is currently authenticated as.** It reads three secrets from the project you name. Check `gcloud config list` before you run it if you work across more than one project.

**A bundle is evidence and PII.** The output directory holds photographs from a real incident, GPS coordinates, timestamps, and camera identifiers. It deserves the same handling as any other case material: encrypted at rest, access limited to people with a reason, retained and destroyed on your agency's schedule. The tool does nothing to protect the bundle after it writes it. That part is yours.

**The manifest is tamper-evidence, not authentication.** There is no signing key. A SHA-256 manifest proves a bundle has not changed since the manifest was written. It does not prove who wrote it, and anyone who can rewrite the bundle can rewrite the manifest. Custody of the sidecar digest, recorded or transmitted separately, is what gives it force. Do not describe it as a signature in a declaration.

**The checksum does not reach the camera.** CalTopo re-encodes uploaded photos, so the file this tool retrieves is a derivative and cannot byte-match the capture file. The README covers what survives the transcode and what does not. Where the imagery itself may be contested, preserve the capturing device.

**EXIF parsing is the untrusted-input surface.** Downloaded images are attacker-influenced in principle: anyone who can upload to a map controls those bytes. `exif.py` is deliberately about a hundred lines of JPEG marker walking that answers one yes/no question, bounds its own loops, reads a capped header, and returns `False` on any parse failure. It must not grow into a general EXIF library. If you need full EXIF extraction, run a maintained parser over the bundle after the fact rather than widening this one.

**Redirects are refused.** The client opens through an opener that raises on any 3xx rather than following it. Nothing external chooses the origin, but a redirect lets the server choose the next URL, and the request signature travels in the query string, so a followed redirect would hand a signed URL to whatever host answered next.

**No network input is trusted for path construction.** Filenames from CalTopo are sanitized before they reach the filesystem, including case-insensitive collision handling, because two photos whose titles differ only by case will silently overwrite each other on macOS and Windows.
