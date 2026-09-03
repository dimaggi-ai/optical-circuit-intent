#!/usr/bin/env python3
"""Fetch the HEDGE hardware-experiment files and refuse any that do not match.

Fifteen files --- three runs, each a transponder CSV and four iperf server
logs --- from github.com/hedge-wan/hedge at the commit pinned in
``ocintent.hedge.HEDGE_COMMIT``. Every file's SHA-256 is checked against
``ocintent.hedge.MANIFEST`` before it is kept, so the pins live in one place.

Fetched rather than vendored: the upstream repository carries no license
grant, so redistribution is not this repository's call to make. Run this
(``make data``) before the tests, the registry, or ``ocintent hedge``.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from ocintent.hedge import HEDGE_COMMIT, HEDGE_RAW_BASE, MANIFEST, sha256_of, verify  # noqa: E402


def main() -> int:
    problems = verify(HERE)
    if not problems:
        print(f"data/hedge: all {len(MANIFEST)} files present and verified against "
              f"commit {HEDGE_COMMIT[:12]}")
        return 0
    for problem in problems:
        rel = problem.path
        target = HERE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        url = f"{HEDGE_RAW_BASE}/{rel}"
        print(f"fetching {rel} ({problem.what})")
        try:
            try:
                with urllib.request.urlopen(url, timeout=60) as response, open(tmp, "wb") as out:
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
            except Exception as exc:  # noqa: BLE001 - report and stop, do not keep a partial file
                print(f"could not fetch {url}: {exc}", file=sys.stderr)
                return 1
            got = sha256_of(tmp)
            if got != MANIFEST[rel]:
                print(f"{rel}: SHA-256 {got} does not match the pin {MANIFEST[rel]}; "
                      f"refusing to keep it", file=sys.stderr)
                return 1
            tmp.replace(target)
        finally:
            # a partial or rejected download never survives, whatever interrupted it
            tmp.unlink(missing_ok=True)
    remaining = verify(HERE)
    if remaining:
        print("still not usable: " + ", ".join(f"{p.path} ({p.what})" for p in remaining),
              file=sys.stderr)
        return 1
    print(f"data/hedge: {len(problems)} file(s) fetched; all {len(MANIFEST)} verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
