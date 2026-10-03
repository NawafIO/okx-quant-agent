"""Remove orphaned partitions left behind by a partition-key change.

Why this exists. The funding partition label was changed from `timeframe=8h` to
`timeframe=funding` (venue fact V-13: the cadence is per-instrument, so the store must not
assert 8h). Renaming a Hive partition key does not move the old data - it orphans it, and
`read_parquet` with a recursive glob happily reads BOTH, so every affected row appeared
twice: 13,532 rows against 7,221 distinct keys.

The lesson is general: **changing a partition key is a migration, not an edit.** Any future
key change needs a prune step like this one, plus the duplicate assertion now in
verify_data_quality.py that catches it if someone forgets.

Dry-run by default. Pass --apply to delete.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

# (dataset, stale timeframe label, replacement) - the keys retired so far.
STALE = [("funding", "8h", "funding")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="paper")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()

    root = Path("data") / args.env / "parquet"
    manifest_path = Path("state") / args.env / "data_manifest.db"

    total_files = 0
    for dataset, stale_tf, replacement in STALE:
        base = root / dataset
        if not base.exists():
            continue
        targets = [d for d in base.rglob(f"timeframe={stale_tf}") if d.is_dir()]
        if not targets:
            print(f"{dataset}: no stale 'timeframe={stale_tf}' partitions")
            continue

        files = [p for d in targets for p in d.rglob("*.parquet")]
        total_files += len(files)
        print(
            f"{dataset}: {len(targets)} stale 'timeframe={stale_tf}' dirs, "
            f"{len(files)} parquet files (superseded by 'timeframe={replacement}')"
        )
        if args.apply:
            for d in targets:
                shutil.rmtree(d, ignore_errors=True)
            if manifest_path.exists():
                with sqlite3.connect(manifest_path) as conn:
                    for table in ("partitions", "gaps", "rejections"):
                        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                        if "timeframe" in cols:
                            conn.execute(
                                f"DELETE FROM {table} WHERE dataset=? AND timeframe=?",
                                (dataset, stale_tf),
                            )
                    conn.commit()
            print(f"  deleted, and purged manifest rows for timeframe={stale_tf}")

    if not args.apply and total_files:
        print(f"\nDRY RUN - {total_files} files would be deleted. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
