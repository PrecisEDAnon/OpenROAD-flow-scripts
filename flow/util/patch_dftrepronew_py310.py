#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path


def patch_file(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    patched = original

    # Python < 3.12 cannot parse f-string expressions that contain a string
    # literal using the same quote type as the surrounding f-string. The
    # dftrepronew harness uses:
    #   f"... {" ".join(...)}"
    # which fails under OpenROAD's embedded Python (3.10).
    patched = patched.replace(
        'file.write(f"group group1 {" ".join(list(group1))}\\n")',
        "file.write(f\"group group1 {' '.join(list(group1))}\\n\")",
    )
    patched = patched.replace(
        'file.write(f"group group2 {" ".join(group2)}\\n")',
        "file.write(f\"group group2 {' '.join(group2)}\\n\")",
    )
    patched = patched.replace(
        "if flop not in group1:",
        "if flop.getName() not in group1:",
    )

    if patched == original:
        return False

    path.write_text(patched, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch dftrepronew to run under Python 3.10 (OpenROAD -python)."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("untracked/dftrepronew/DFTRepro"),
        help="Path to the dftrepronew DFTRepro directory (default: %(default)s).",
    )
    args = parser.parse_args()

    target = args.root / "7_groups" / "run_ord.py"
    if not target.exists():
        raise SystemExit(f"error: expected file not found: {target}")

    changed = patch_file(target)
    if changed:
        print(f"patched: {target}")
    else:
        print(f"no changes needed: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
