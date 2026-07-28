#!/usr/bin/env python3
"""Subscribe to herdr's event feed and dump every frame with a wall clock stamp.

Serves epic #667 UAT checks P1/P2/P3 (which event carries an agent_status
transition, and does `revision` advance) and later W2-W7. Deliberately dumb: it
records, it does not interpret. Interpretation happens in the check, against the
recorded file, so a wrong reading can be re-read rather than re-run.

Usage: probe.py <out.jsonl> [seconds]
"""
import json
import os
import socket
import sys
import time

# The key is `type`, NOT `event`. Verified live 2026-07-28: an `event` key is
# rejected with `invalid_request: missing field 'type'` AND the server closes the
# connection, so one malformed subscription kills the whole feed. This matches
# what hooks/pane_watch_lib.py:116 build_subscriptions already emits.
SUBS = ["pane.updated", "pane.created", "pane.closed"]


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out_path, duration = argv[1], float(argv[2]) if len(argv) > 2 else 180.0
    sock_path = os.environ.get("HERDR_SOCKET_PATH")
    if not sock_path:
        print("HERDR_SOCKET_PATH unset", file=sys.stderr)
        return 3

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5.0)
    client.connect(sock_path)
    subs = os.environ.get("UAT_SUBS")
    names = subs.split(",") if subs else SUBS
    req = {"id": "uat667-probe", "method": "events.subscribe",
           "params": {"subscriptions": [{"type": e} for e in names]}}
    client.sendall((json.dumps(req) + "\n").encode())

    deadline = time.time() + duration
    buf = b""
    n = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        # Record the subscribe request itself so the evidence file is
        # self-describing — a frame count means nothing without the ask.
        fh.write(json.dumps({"_probe": "request", "request": req}) + "\n")
        fh.flush()
        while time.time() < deadline:
            try:
                client.settimeout(max(0.5, deadline - time.time()))
                chunk = client.recv(65536)
            except socket.timeout:
                continue
            except OSError as exc:
                fh.write(json.dumps({"_probe": "socket_error", "error": str(exc)}) + "\n")
                break
            if not chunk:
                fh.write(json.dumps({"_probe": "eof"}) + "\n")
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                n += 1
                rec = {"_probe": "frame", "n": n, "ts": time.time(),
                       "raw": line.decode("utf-8", "replace")}
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
    client.close()
    print(f"{n} frames -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
