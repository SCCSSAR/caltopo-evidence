"""
client.py — read-only CalTopo Team API client.

READ-ONLY BY CONSTRUCTION
    This module issues GET requests and nothing else. There is deliberately no
    POST/PUT/DELETE code path. The maps this tool reads are operational SAR
    records and, in the discoverable case, evidence; an accidental write is
    unrecoverable and would also compromise the record's integrity.

DEPENDENCIES
    Standard library only. No requests, no httpx. Keeps the tool installable
    anywhere Python 3.9+ runs and keeps the audit surface of an evidence tool
    as small as possible.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

DEFAULT_BASE_URL = "https://caltopo.com"

# Endpoints, established empirically 2026-08-19 against a live map. Ten other
# plausible download paths were probed and rejected; see docs/caltopo-api-notes.md.
PATH_MAP_STATE = "/api/v1/map/{map_id}/since/-1"
PATH_MEDIA_META = "/api/v1/media/{media_id}"
PATH_MEDIA_BYTES = "/api/v1/media/{media_id}/original"
# The map's own title/mode/sharing live on the ACCOUNT-scoped object, not on the
# map-state payload, so reading the human-readable map name requires a team id.
PATH_MAP_INFO = "/api/v1/acct/{team_id}/CollaborativeMap/{map_id}"

# Bounded retry for TRANSIENT server errors only. Mirrors the reasoning used in
# the SCCSSAR dispatch client: a 5xx is the server actively reporting failure,
# so retrying is sound. A TIMEOUT is the ambiguous case and costs the full
# budget, so it is not retried. 4xx is never retried -- it will not become true.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = (0.5, 1.5)          # exactly MAX_ATTEMPTS - 1 entries

# Guard against a hostile or broken upstream exhausting memory on the JSON
# paths. The BYTES path is streamed and is deliberately not subject to this.
MAX_JSON_BYTES = 64 * 1024 * 1024


class CalTopoError(RuntimeError):
    """Non-retriable CalTopo API failure."""


class CalTopoAuthError(CalTopoError):
    """401/403 -- credentials rejected or the map is not visible to this team."""


@dataclass(frozen=True)
class Credentials:
    credential_id: str
    credential_secret: str          # base64, as CalTopo issues it
    team_id: str = ""

    def validate(self) -> None:
        if not self.credential_id or not self.credential_secret:
            raise CalTopoError(
                "Missing credentials.\n"
                "  Either export them:\n"
                "    export CALTOPO_CREDENTIAL_ID=...\n"
                "    export CALTOPO_CREDENTIAL_SECRET=...\n"
                "    export CALTOPO_TEAM_ID=...            # optional, for the map title\n"
                "  Or read them from Google Secret Manager. Note the flag goes AFTER\n"
                "  the subcommand:\n"
                "    python3 -m caltopo_evidence extract <MAP_ID> --from-gcloud <PROJECT>"
            )
        try:
            base64.b64decode(self.credential_secret)
        except Exception as exc:                              # noqa: BLE001
            raise CalTopoError(
                "CALTOPO_CREDENTIAL_SECRET is not valid base64."
            ) from exc


def _validated_base_url(base_url: str) -> str:
    """
    Accept only an https:// origin.

    `--base-url` is an operator-supplied string that reaches urllib. Without a
    scheme check, `file:///etc/passwd` makes urlopen read a local path while the
    tool still believes it is talking to CalTopo, and a plain http:// origin
    would put a signed credential on the wire in cleartext. Neither is a remote
    attack -- the operator types this -- but an evidence tool should not be
    talkable into either by a typo or a copied command line.
    """
    cleaned = base_url.rstrip("/")
    if not cleaned.lower().startswith("https://"):
        raise CalTopoError(
            f"--base-url must be an https:// origin, got {base_url!r}. "
            "Requests carry a signed credential, so http:// and non-network "
            "schemes such as file:// are refused."
        )
    return cleaned


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """
    Refuse to follow redirects.

    This is the actual SSRF vector for a client like this one. The origin is a
    module constant and the paths are fixed templates, so nothing external
    chooses the URL -- but a redirect lets the SERVER choose the next one, and
    a 302 toward a link-local metadata address is the classic pivot. A
    read-only client that talks to exactly one known host gains nothing by
    following one.

    It also protects the credential: the signature is carried in the query
    string, so a followed redirect would hand a signed URL to whatever host
    answered next.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CalTopoError(
            f"Refusing to follow an HTTP {code} redirect from {_redact(req.selector)}. "
            "This client talks only to the configured CalTopo origin."
        )


class CalTopoReadOnlyClient:
    """Signed, read-only access to the CalTopo Team API."""

    def __init__(self, creds: Credentials, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 120.0):
        creds.validate()
        self._creds = creds
        self.base_url = _validated_base_url(base_url)
        self.timeout = timeout
        self._ssl = ssl.create_default_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl), _NoRedirects,
        )

    # -- signing ---------------------------------------------------------

    def _sign(self, path: str, expires_ms: int) -> str:
        """
        CalTopo GET signing string: "GET {path}\\n{expires_ms}\\n".

        The trailing newline and the empty payload are both load-bearing, and
        the signature covers the PATH ONLY -- not the query string that carries
        the auth parameters. Confirmed live 2026-08-19.
        """
        data = f"GET {path}\n{expires_ms}\n"
        raw = base64.b64decode(self._creds.credential_secret)
        digest = hmac.new(raw, data.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _signed_url(self, path: str) -> str:
        expires_ms = int(time.time() * 1000) + 120_000
        qs = urllib.parse.urlencode({
            "id": self._creds.credential_id,
            "expires": str(expires_ms),
            "signature": self._sign(path, expires_ms),
        })
        return f"{self.base_url}{path}?{qs}"

    # -- transport -------------------------------------------------------

    def _open(self, path: str):
        req = urllib.request.Request(
            self._signed_url(path), method="GET",
            headers={"User-Agent": f"caltopo-evidence/{__import__('caltopo_evidence').__version__}"},
        )
        # Deliberately an opener rather than urlopen(): urlopen follows
        # redirects, and this one must not. See _NoRedirects.
        return self._opener.open(req, timeout=self.timeout)

    def _request_with_retry(self, path: str, handler: Callable):
        """
        Run `handler(response)` under the transient-5xx retry policy.

        The handler is invoked inside the open connection so a streaming caller
        can consume the body without it being buffered first.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                with self._open(path) as resp:
                    return handler(resp)
            except urllib.error.HTTPError as e:
                body = e.read(4096)
                if e.code in (401, 403):
                    raise CalTopoAuthError(
                        f"CalTopo returned {e.code} for {_redact(path)}. "
                        "Check the credentials and that the map belongs to this team."
                    ) from e
                if 500 <= e.code < 600 and attempt < MAX_ATTEMPTS - 1:
                    time.sleep(RETRY_BACKOFF_S[attempt])
                    last_exc = e
                    continue
                raise CalTopoError(
                    f"CalTopo returned {e.code} for {_redact(path)}: "
                    f"{body[:200]!r}"
                ) from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # Deliberately NOT retried: a timeout is ambiguous (the request
                # may have been served) and costs the whole timeout budget.
                raise CalTopoError(
                    f"Network failure for {_redact(path)}: {type(e).__name__}: {e}"
                ) from e
        raise CalTopoError(f"Exhausted retries for {_redact(path)}: {last_exc}")

    # -- API surface -----------------------------------------------------

    def get_json(self, path: str) -> dict:
        def handler(resp):
            body = resp.read(MAX_JSON_BYTES)
            if not body:
                # A 200 with an empty body is a real CalTopo response shape on
                # at least one neighbouring endpoint. Treating it as success
                # would silently produce an empty result.
                raise CalTopoError(f"Empty 200 response from {_redact(path)}")
            return json.loads(body)
        return self._request_with_retry(path, handler)

    def get_map_state(self, map_id: str) -> dict:
        return self.get_json(PATH_MAP_STATE.format(map_id=map_id))

    def get_map_info(self, map_id: str) -> dict:
        """
        Map title / mode / sharing / description.

        Requires a team id. Returns {} when none is configured rather than
        failing the run: the map name is a quality-of-life field and losing it
        must not cost the evidence bundle. The caller records the omission.
        """
        if not self._creds.team_id:
            return {}
        path = PATH_MAP_INFO.format(team_id=self._creds.team_id, map_id=map_id)
        try:
            return self.get_json(path)
        except CalTopoError:
            return {}

    def get_media_metadata(self, media_id: str) -> dict:
        return self.get_json(PATH_MEDIA_META.format(media_id=media_id))

    def download_media(self, media_id: str, dest: Path,
                       expected_bytes: Optional[int] = None,
                       chunk_size: int = 1024 * 1024) -> tuple[int, str]:
        """
        Stream one original-resolution media file to `dest`.

        Returns (bytes_written, sha256_hex).

        Streams rather than buffers: a single photo runs to ~8 MB and a map to
        several hundred, so buffering would put the whole bundle in memory for
        no benefit. The digest is computed during the stream, so the bytes are
        hashed exactly as written and are never read back to be trusted twice.

        Writes to a `.part` file and renames only after the size check passes,
        so an interrupted run can never leave a truncated file that looks
        complete to a later resume.
        """
        path = PATH_MEDIA_BYTES.format(media_id=media_id)
        part = dest.with_suffix(dest.suffix + ".part")

        def handler(resp):
            digest = hashlib.sha256()
            written = 0
            with part.open("wb") as fh:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    written += chunk_size and len(chunk)
            return written, digest.hexdigest()

        try:
            written, sha = self._request_with_retry(path, handler)
        except Exception:
            part.unlink(missing_ok=True)
            raise

        if written == 0:
            part.unlink(missing_ok=True)
            raise CalTopoError(f"Downloaded 0 bytes for media {_redact(media_id)}")
        if expected_bytes is not None and written != expected_bytes:
            part.unlink(missing_ok=True)
            raise CalTopoError(
                f"Size mismatch for media {_redact(media_id)}: "
                f"got {written}, CalTopo declared {expected_bytes}"
            )
        part.replace(dest)
        return written, sha


def _redact(s: str) -> str:
    """Keep opaque ids out of error text -- they are handles into evidence."""
    return s if len(s) < 24 else s[:8] + "..." + s[-4:]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
