"""Provision and revoke restricted Friend Beta access grants.

Run from ``gateway`` with the production environment already loaded. The issue
command is the only operation that reveals an access code; database rows and
list output contain digest-free metadata only.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Sequence

from app.auth import (
    FRIEND_BETA_DEFAULT_SCOPES,
    create_friend_beta_access_grant,
    list_friend_beta_access_grants,
    revoke_friend_beta_access_grant,
)


def _scope_list(value: str) -> list[str]:
    scopes = [item.strip() for item in value.split(",") if item.strip()]
    if not scopes:
        raise argparse.ArgumentTypeError("at least one scope is required")
    return scopes


def _prepare_output_path(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise ValueError("--output must be an absolute path")
    resolved = path.resolve(strict=False)
    repository_root = Path(__file__).resolve().parents[2]
    if resolved == repository_root or repository_root in resolved.parents:
        raise ValueError("--output must be outside the repository checkout")
    if path.exists() or path.is_symlink():
        raise ValueError("--output must not already exist")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _write_once(path: Path, payload: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.unlink(missing_ok=True)
        raise ValueError("the access-code output could not be restricted to mode 0600")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision independently revocable Friend Beta access",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue = subparsers.add_parser("issue", help="issue one per-person/device access code")
    issue.add_argument("--user-id", required=True)
    issue.add_argument(
        "--scopes", type=_scope_list,
        default=sorted(FRIEND_BETA_DEFAULT_SCOPES),
        help="comma-separated scopes (default: read,account,notifications)",
    )
    issue.add_argument("--ttl-hours", type=int, default=7 * 24)
    issue.add_argument(
        "--allow-shared-runtime-access", action="store_true",
        help="acknowledge that command/voice scopes reach the shared operator runtime",
    )
    issue.add_argument(
        "--output", required=True,
        help="new absolute mode-0600 file that receives the one displayed access code",
    )

    listing = subparsers.add_parser("list", help="list metadata without codes or hashes")
    listing.add_argument("--user-id")

    revoke = subparsers.add_parser("revoke", help="revoke a grant and all derived sessions")
    revoke.add_argument("--grant-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "issue":
            output_path = _prepare_output_path(args.output)
            grant = create_friend_beta_access_grant(
                args.user_id,
                scopes=args.scopes,
                ttl_seconds=args.ttl_hours * 3600,
                allow_shared_runtime_access=args.allow_shared_runtime_access,
            )
            try:
                _write_once(output_path, grant)
            except Exception:
                # If delivery cannot be made exclusive and mode-0600, retire
                # the now-unrecoverable code rather than leaving a live orphan.
                revoke_friend_beta_access_grant(str(grant["grant_id"]))
                raise
            public = {key: value for key, value in grant.items() if key != "access_code"}
            print(json.dumps({**public, "access_code_file": str(output_path)}, sort_keys=True))
            print(
                "Access code written once; transfer it out of band, then delete the file after enrollment.",
                file=sys.stderr,
            )
            return 0
        if args.command == "list":
            print(json.dumps(list_friend_beta_access_grants(user_id=args.user_id), sort_keys=True))
            return 0
        if args.command == "revoke":
            revoked = revoke_friend_beta_access_grant(args.grant_id)
            print(json.dumps({"grant_id": args.grant_id, "status": "revoked" if revoked else "not-active"}, sort_keys=True))
            return 0 if revoked else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"friend-beta access error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
