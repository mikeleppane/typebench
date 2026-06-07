"""A controllable fake type checker. Prints a JSON summary and exits with a
chosen code — optionally after a sleep, or by killing itself with a signal —
so the engine can be tested without any real checker. Ships in the package so
the stub adapter works from an installed wheel, not only a source checkout."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(prog="typebench._fake_checker")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--diagnostics", type=int, default=0)
    parser.add_argument("--files", type=int, default=0)
    parser.add_argument(
        "--signal",
        type=int,
        default=None,
        help="If set, kill self with this signal (9 = OOM-like, 11 = crash).",
    )
    ns = parser.parse_args()

    if ns.sleep:
        time.sleep(ns.sleep)
    if ns.signal is not None:
        os.kill(os.getpid(), ns.signal)
    print(json.dumps({"diagnostics": ns.diagnostics, "files": ns.files}))
    return ns.exit_code


if __name__ == "__main__":
    sys.exit(main())
