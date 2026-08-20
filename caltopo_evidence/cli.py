"""
cli.py — command line interface.

    caltopo-evidence extract <MAP_ID> [--out DIR] [--all|--limit N] [--resume|--force]
    caltopo-evidence verify  <BUNDLE_DIR>

CREDENTIALS
    Read from the environment by default -- CALTOPO_CREDENTIAL_ID,
    CALTOPO_CREDENTIAL_SECRET, and optionally CALTOPO_TEAM_ID (needed only to
    resolve the human-readable map title).

    --from-gcloud PROJECT is a convenience for operators who keep the
    credentials in Google Secret Manager. It is opt-in so the default path
    carries no cloud-provider assumption.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import TOOL_NAME, __version__
from .client import CalTopoError, CalTopoReadOnlyClient, Credentials
from .extract import BundleExists, extract
from .manifest import verify_bundle

GCLOUD_SECRETS = {
    "credential_id": "caltopo-credential-id",
    "credential_secret": "caltopo-credential-secret",
    "team_id": "caltopo-team-id",
}


def _gcloud_secret(secret: str, project: str, gcloud: str) -> str:
    proc = subprocess.run(
        [gcloud, "secrets", "versions", "access", "latest",
         "--secret", secret, "--project", project],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise CalTopoError(
            f"Could not read secret '{secret}' from project '{project}'.\n"
            f"  gcloud said: {proc.stderr.strip()}\n"
            f"  If the token has expired, run: gcloud auth login <account>"
        )
    return proc.stdout.strip()


def load_credentials(args) -> Credentials:
    if args.from_gcloud:
        g = args.gcloud_path
        return Credentials(
            credential_id=_gcloud_secret(GCLOUD_SECRETS["credential_id"], args.from_gcloud, g),
            credential_secret=_gcloud_secret(GCLOUD_SECRETS["credential_secret"], args.from_gcloud, g),
            team_id=_gcloud_secret(GCLOUD_SECRETS["team_id"], args.from_gcloud, g),
        )
    return Credentials(
        credential_id=os.environ.get("CALTOPO_CREDENTIAL_ID", "").strip(),
        credential_secret=os.environ.get("CALTOPO_CREDENTIAL_SECRET", "").strip(),
        team_id=os.environ.get("CALTOPO_TEAM_ID", "").strip(),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Read-only extraction of photos and metadata from a CalTopo map.",
    )
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", help="extract photos, CSV report and manifest")
    ex.add_argument("map_id", help="CalTopo map id, e.g. the XXXXX in caltopo.com/m/XXXXX")
    ex.add_argument("--out", default="bundles", type=Path,
                    help="root directory for bundles (default: ./bundles)")
    ex.add_argument("--metadata-only", action="store_true",
                    help="read the map and write the CSV, but download no image bytes")
    ex.add_argument("--limit", type=int, default=None,
                    help="download only the first N photos — produces a PARTIAL bundle")
    ex.add_argument("--all", action="store_true",
                    help="download every photo (this is the default; kept for clarity)")
    ex.add_argument("--resume", action="store_true",
                    help="continue an interrupted extraction, keeping verified files")
    ex.add_argument("--force", action="store_true",
                    help="replace an existing bundle")
    ex.add_argument("--operator", default=None,
                    help="operator identity recorded in the manifest (default: user@host)")
    ex.add_argument("--quiet", action="store_true")
    _add_cred_args(ex)

    vf = sub.add_parser("verify", help="re-verify a bundle against its manifest")
    vf.add_argument("bundle_dir", type=Path)
    return p


def _add_cred_args(sp) -> None:
    sp.add_argument("--from-gcloud", metavar="PROJECT", default=None,
                    help="read credentials from Google Secret Manager in PROJECT")
    sp.add_argument("--gcloud-path", default=os.environ.get("GCLOUD_PATH", "gcloud"),
                    help="path to the gcloud binary (default: gcloud, or $GCLOUD_PATH)")


def cmd_extract(args) -> int:
    creds = load_credentials(args)
    # The base URL is deliberately NOT settable from the command line. It was a
    # `--base-url` flag, undocumented and unused, and it was the only path by
    # which anything outside this code reached urllib. Tests that need a
    # different origin pass `base_url=` to the constructor directly.
    client = CalTopoReadOnlyClient(creds)

    # DOWNLOADING IS THE DEFAULT.
    #
    # It was originally metadata-only, on the reasoning that pulling hundreds of
    # megabytes should be deliberate. That reasoning was wrong in practice: the
    # command is called "extract" and its whole purpose is to get the photos, so
    # a run that quietly fetches none violates the plainest reading of what the
    # operator asked for. Observed live -- two consecutive runs, no photos, and
    # the second used --force in the reasonable belief that it meant "actually
    # do it". Size is handled by telling the operator what is coming, not by
    # withholding the thing they asked for.
    if args.metadata_only and args.limit is not None:
        print("error: --metadata-only and --limit are mutually exclusive", file=sys.stderr)
        return 2
    if args.metadata_only and args.all:
        print("error: --metadata-only and --all are mutually exclusive", file=sys.stderr)
        return 2
    limit = 0 if args.metadata_only else args.limit      # None means "every photo"

    def progress(i, total):
        if not args.quiet and (i == total or i % 10 == 0):
            print(f"    {i}/{total}", flush=True)

    result = extract(client, args.map_id, args.out, limit=limit,
                     resume=args.resume, force=args.force,
                     operator=args.operator, quiet=args.quiet, progress=progress)
    if result.notes and not args.quiet:
        print("\n  Notes recorded in the manifest:")
        for n in result.notes:
            print(f"    - {n}")
    failed = [r for r in result.records if r.download_status.startswith("failed")]
    return 1 if failed else 0


def cmd_verify(args) -> int:
    res = verify_bundle(args.bundle_dir)
    if res["ok"]:
        print(f"VERIFIED — {res['checked']} artifact(s) match the manifest.")
        print("Note: SHA-256 digests are tamper-evidence, not a signature.")
        return 0
    print(f"FAILED — {len(res['problems'])} problem(s):")
    for p in res["problems"]:
        print(f"  - {p}")
    return 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "extract":
            return cmd_extract(args)
        if args.command == "verify":
            return cmd_verify(args)
    except BundleExists as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except CalTopoError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted — re-run with --resume to continue", file=sys.stderr)
        return 130
    return 2
