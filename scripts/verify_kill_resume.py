"""M1 acceptance: forced process kill mid-backfill, then correct resume.

The unit suite simulates a kill *inside* the write call. This is the stronger claim the
acceptance criterion actually makes: spawn the real CLI as a separate process, kill it
with SIGKILL-equivalent force partway through, and then verify that

  1. no torn Parquet file was published (every file opens and parses),
  2. no orphaned .tmp file is left behind after the next run sweeps,
  3. the manifest agrees with what is physically on disk,
  4. a resumed run completes and the store is intact.

Runs against the DEMO tree so it cannot disturb a PAPER backfill in progress - which also
exercises the per-environment physical isolation from architecture §6.2.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

ENV = "DEMO"
DATA = Path("data/demo")
STATE = Path("state/demo")
PY = Path(".venv/Scripts/python.exe")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def parquet_files() -> list[Path]:
    return sorted(DATA.rglob("*.parquet"))


def spawn() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            str(PY),
            "-m",
            "okxq.data.cli",
            "backfill",
            "--env",
            ENV,
            "--symbols",
            "6",
            "--symbols-5m",
            "0",
            "--years",
            "3",
            "--timeframes",
            "1d",
            "1h",
            "--no-mark-index",
            "--skip-funding",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


print("=" * 78)
print("M1 ACCEPTANCE: forced-kill resume integrity")
print("=" * 78)

# Clean slate so the test measures what it claims to.
for path in (DATA, STATE):
    if path.exists():
        shutil.rmtree(path)

print("\n1. Start a real backfill process, then kill it mid-run")
proc = spawn()
# Wait until it has actually written something, so the kill lands during real work
# rather than during startup.
deadline = time.time() + 180
wrote = 0
while time.time() < deadline:
    wrote = len(parquet_files())
    if wrote >= 15:
        break
    if proc.poll() is not None:
        break
    time.sleep(0.5)

if proc.poll() is None:
    # Hard kill - no cleanup handlers, the way a power loss or Task Manager kill behaves.
    proc.kill()
    proc.wait(timeout=30)
    print(f"  killed after {wrote} parquet files were written (pid {proc.pid})")
    killed = True
else:
    print(f"  process finished before it could be killed ({wrote} files)")
    killed = False

check("process was killed mid-run", killed and wrote >= 15, f"{wrote} files at kill time")

print("\n2. No torn Parquet published - every file must open and parse")
unreadable: list[str] = []
total_rows = 0
for path in parquet_files():
    try:
        total_rows += pq.read_table(path).num_rows
    except Exception as exc:
        unreadable.append(f"{path.name}: {type(exc).__name__}")
check(
    "all parquet files readable after hard kill",
    not unreadable,
    f"{len(parquet_files())} files, {total_rows:,} rows"
    + (f", UNREADABLE: {unreadable[:3]}" if unreadable else ""),
)

print("\n3. Manifest never claims more than is on disk")
manifest_db = STATE / "data_manifest.db"
if manifest_db.exists():
    with duckdb.connect() as conn:
        conn.execute("INSTALL sqlite; LOAD sqlite;")
        recorded = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(rows),0) FROM "
            f"sqlite_scan('{manifest_db.as_posix()}', 'partitions')"
        ).fetchone()
    print(f"  manifest records {recorded[0]} partitions / {recorded[1]:,} rows")
    print(f"  disk holds        {len(parquet_files())} files / {total_rows:,} rows")
    # A crash may leave files on disk that are not yet recorded (work to redo), but the
    # manifest must never record a partition whose file is absent or short.
    check(
        "manifest does not over-claim",
        recorded[1] <= total_rows,
        f"recorded {recorded[1]:,} <= on-disk {total_rows:,}",
    )
else:
    check("manifest exists", False, "no manifest written")

print("\n4. Resume: re-run to completion")
t0 = time.time()
result = subprocess.run(
    [
        str(PY),
        "-m",
        "okxq.data.cli",
        "backfill",
        "--env",
        ENV,
        "--symbols",
        "6",
        "--symbols-5m",
        "0",
        "--years",
        "3",
        "--timeframes",
        "1d",
        "1h",
        "--no-mark-index",
        "--skip-funding",
    ],
    capture_output=True,
    text=True,
)
elapsed = time.time() - t0
check("resumed run exited cleanly", result.returncode == 0, f"exit {result.returncode}")
swept = "swept orphaned temp files" in result.stdout + result.stderr
print(f"  resumed in {elapsed:.0f}s; orphan sweep triggered: {swept}")

print("\n5. Post-resume integrity")
check("no orphaned temp files remain", not list(DATA.rglob("*.tmp")), "")

unreadable = []
final_rows = 0
for path in parquet_files():
    try:
        final_rows += pq.read_table(path).num_rows
    except Exception:
        unreadable.append(path.name)
check(
    "all parquet readable after resume",
    not unreadable,
    f"{len(parquet_files())} files, {final_rows:,} rows",
)
check("resume added data", final_rows >= total_rows, f"{total_rows:,} -> {final_rows:,}")

# Independent re-check that the resumed store is still internally consistent.
glob = (DATA / "parquet" / "ohlcv" / "**" / "*.parquet").as_posix()
with duckdb.connect() as conn:
    bad = conn.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{glob}', hive_partitioning=true)
        WHERE high < low OR high < open OR high < close OR low > open OR low > close
    """).fetchone()[0]
    dupes = conn.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT inst_id, timeframe, ts_open_ms, COUNT(*) n
            FROM read_parquet('{glob}', hive_partitioning=true)
            GROUP BY 1,2,3 HAVING n > 1)
    """).fetchone()[0]
check("no OHLC violations after resume", bad == 0, f"{bad} rows")
check("no duplicate bars after resume", dupes == 0, f"{dupes} keys")

print("\n" + "=" * 78)
if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
print(f"\n(cleaning up {DATA} and {STATE})")
for path in (DATA, STATE):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
