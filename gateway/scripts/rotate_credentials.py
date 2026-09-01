"""Validate or apply a bounded rewrite of encrypted OAuth credentials.

The gateway's current and previous key settings must be supplied through the
environment. This command is dry-run by default; pass --apply only after
reviewing the reported count.
"""

import argparse
import os

MAX_ROTATION_ROWS = 1_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--db-path',
        help='SQLite database path (must be set before importing the gateway)',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=MAX_ROTATION_ROWS,
        help=f'maximum rows to inspect (1-{MAX_ROTATION_ROWS})',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='commit the rewrite; without this flag the command is a dry run',
    )
    parser.add_argument(
        '--migrate-legacy',
        action='store_true',
        help='rewrite explicitly authorized unversioned legacy values instead of rotating vN values',
    )
    args = parser.parse_args()

    if args.db_path:
        # db.py reads this value during import. Keeping the option here avoids
        # putting a database path or credential in shell history or source.
        os.environ['MAGISTRATE_DB_PATH'] = args.db_path

    from app.db import migrate_legacy_oauth_credentials, rotate_oauth_credentials

    if args.migrate_legacy:
        report = migrate_legacy_oauth_credentials(limit=args.limit, apply=args.apply)
        action = 'migrated' if args.apply else 'eligible for migration'
    else:
        report = rotate_oauth_credentials(limit=args.limit, apply=args.apply)
        action = 'rewritten' if args.apply else 'eligible for rewrite'
    print(f'scanned={report.scanned} {action}={report.rewritten}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
