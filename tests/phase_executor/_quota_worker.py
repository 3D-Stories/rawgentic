"""Subprocess worker for the cross-process quota test. Acquires one permit, holds it, and
records the held interval [start,end] to the output file. Relies on PYTHONPATH for the import."""
import sys
import time

from phase_executor.quota import QuotaCoordinator


def main():
    root, pool, limit, hold, out = sys.argv[1:6]
    qc = QuotaCoordinator(root, {pool: int(limit)})
    with qc.acquire(pool, timeout=30.0, poll=0.02):
        start = time.time()
        time.sleep(float(hold))
        end = time.time()
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{start} {end}\n")


if __name__ == "__main__":
    main()
