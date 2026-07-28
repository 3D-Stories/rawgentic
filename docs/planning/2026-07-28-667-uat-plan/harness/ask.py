#!/usr/bin/env python3
"""Send one request to the herdr socket, print the first response(s), exit.

For discovering the live-event contract (epic #667 UAT). Each call is its own
connection, so a rejected request that closes the socket cannot poison the next probe.

Usage: ask.py '<json request>' [seconds_to_listen]
"""
import json
import os
import socket
import sys
import time


def main(argv):
    req = argv[1]
    listen = float(argv[2]) if len(argv) > 2 else 2.0
    path = os.environ["HERDR_SOCKET_PATH"]
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(3.0)
    c.connect(path)
    c.sendall((req.strip() + "\n").encode())
    end = time.time() + listen
    buf = b""
    n = 0
    while time.time() < end:
        try:
            c.settimeout(max(0.3, end - time.time()))
            chunk = c.recv(65536)
        except socket.timeout:
            continue
        except OSError as e:
            print(f"  [socket error: {e}]")
            break
        if not chunk:
            print("  [EOF — server closed]")
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            n += 1
            if n <= 4:
                print(f"  <- {line.decode('utf-8','replace')[:420]}")
    print(f"  [{n} line(s) in {listen}s]")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
