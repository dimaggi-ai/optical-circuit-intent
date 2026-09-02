#!/usr/bin/env python3
"""Run every example input and check it reaches the result the docs claim.

Each example carries its expected exit code, so a change that quietly flips an
example from admitted to refused fails here rather than in a reader's terminal.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from typing import Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from ocintent.cli import main as cli_main  # noqa: E402

#: (argv, expected exit code, what the example is here to show)
CASES: Tuple[Tuple[Tuple[str, ...], int, str], ...] = (
    (("compile", "examples/intent-request-800g.json"), 0,
     "a request with a stated hold compiles to reserve, connect, verify"),
    (("compile", "examples/intent-request-800g.json", "--boundary", "between-checkpoints"),
     0, "the same request, pinned to a boundary the plan carries forward"),
    (("compile", "examples/intent-failover.json"), 0,
     "a failover verifies the replacement before tearing the old path down"),
    (("compile", "examples/intent-no-hold.json"), 2,
     "a request with no hold time is unreadable, not admitted"),
    (("ladder",), 0,
     "the retune ladder at the reference rhythm, and the two objectives disagreeing"),
    (("ladder", "--retune-s", "0.025"), 0,
     "a MEMS-speed retune, where the objectives agree and the ladder is moot"),
    (("disagree",), 0,
     "the exact set of retune times where the objectives disagree"),
    (("radix", "examples/switch-hall-a.json"), 1,
     "a switch with free ports and demand it cannot serve"),
    (("checkpoint",), 0,
     "the four checkpoint strategies priced against the reference rhythm"),
    (("checkpoint", "--stitch-gbps", "3200"), 0,
     "the same job on a wide stitch, where the cheapest answer has changed"),
    (("drift", "examples/circuit-drifted.json"), 1,
     "800G at 8 us declared, 40 us measured: the campus bug"),
    (("drift", "examples/circuit-drifted.json", "--il-rate", "0.9"), 1,
     "the same circuit with a measured loss trend and a crossing date"),
    (("drift", "examples/circuit-healthy.json"), 0,
     "a circuit whose measurement matches what the inventory declares"),
    (("drift", "examples/circuit-wrong-id.json"), 1,
     "the plant and the inventory disagree about which circuit this is"),
    (("ledger", "examples/ledger.json", "--now-s", "10368000"), 0,
     "the capacity-debt report, aged"),
    (("ledger", "examples/ledger.json", "--now-s", "10368000", "--budget", "100000"), 1,
     "the same report against a budget it exceeds"),
    (("example", "rhythm"), 0,
     "the reference rhythm, as JSON"),
)


def main() -> int:
    os.chdir(ROOT)
    width = max(len(" ".join(argv)) for argv, _, _ in CASES)
    failures = []
    for argv, expected, description in CASES:
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                got = cli_main(list(argv))
        except SystemExit as exc:  # argparse exits on a bad input
            got = int(exc.code or 0)
        except Exception as exc:  # noqa: BLE001
            got = f"raised {type(exc).__name__}: {exc}"
        ok = got == expected
        mark = "ok  " if ok else "FAIL"
        print(f"[{mark}] ocintent {' '.join(argv):<{width}}  exit {got} "
              f"(want {expected})")
        print(f"        {description}")
        if not ok:
            failures.append((argv, expected, got, buf.getvalue()))
    print()
    print(f"{len(CASES) - len(failures)}/{len(CASES)} examples reach their "
          f"documented result")
    for argv, expected, got, output in failures:
        print()
        print(f"--- ocintent {' '.join(argv)}: wanted {expected}, got {got}")
        print(output.rstrip())
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
