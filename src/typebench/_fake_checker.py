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
from pathlib import Path


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
    parser.add_argument("--fail-after-runs", type=int, default=None)
    parser.add_argument("--state-file", type=str, default=None)
    ns = parser.parse_args()

    if ns.fail_after_runs is not None and ns.state_file is not None:
        state = Path(ns.state_file)
        count = int(state.read_text()) if state.exists() else 0
        count += 1
        state.write_text(str(count))
        if count > ns.fail_after_runs:
            return 2  # crash on later invocations: probe succeeds, timed runs fail

    if ns.sleep:
        time.sleep(ns.sleep)
    if ns.signal is not None:
        os.kill(os.getpid(), ns.signal)
    print(json.dumps({"diagnostics": ns.diagnostics, "files": ns.files}))
    return ns.exit_code


if __name__ == "__main__":
    sys.exit(main())
