"""Tidy calls/ before submitting: drop calls that aren't valid full conversations and
renumber the rest 01, 02, 03... in the order they were made.

    python scripts/renumber_calls.py            # preview only, changes nothing
    python scripts/renumber_calls.py --apply    # do it

Updates the call id inside transcript.txt / transcript.json / findings.md to match the
new folder name, then rewrites calls/INDEX.md and reports/check_matrix.md.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from caller.config import CALLS_DIR  # noqa: E402
from run_calls import check_call, write_index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually delete and rename")
    args = parser.parse_args()

    calls = sorted(
        (p for p in CALLS_DIR.iterdir() if p.is_dir() and re.match(r"\d+-", p.name)),
        key=lambda p: int(p.name.split("-", 1)[0]),
    )
    keep, drop = [], []
    for d in calls:
        problems = check_call(d)
        (drop if problems else keep).append((d, problems))

    plan = []
    for i, (d, _) in enumerate(keep, start=1):
        new = f"{i:02d}-{d.name.split('-', 1)[1]}"
        plan.append((d, new))

    print("Delete (not valid full conversations):")
    for d, problems in drop or [(None, None)]:
        print(f"  {d.name}  ({'; '.join(problems)[:90]})" if d else "  (none)")
    print("\nKeep and renumber:")
    for d, new in plan:
        print(f"  {d.name:35s} -> {new}" if d.name != new else f"  {d.name:35s}    (unchanged)")

    if not args.apply:
        print("\nPreview only. Run with --apply to make these changes.")
        return

    for d, _ in drop:
        shutil.rmtree(d)
    # two passes so a new name never collides with an existing folder
    temp = []
    for d, new in plan:
        t = d.with_name(f"_tmp_{new}")
        d.rename(t)
        temp.append((t, new))
    for t, new in temp:
        final = t.with_name(new)
        t.rename(final)
        _update_call_id(final, new)

    print(f"\nDone: {write_index()} valid calls in calls/INDEX.md")
    print("Now run: python scripts/analyze_calls.py   (rebuilds reports/ with the new names)")


def _update_call_id(d: Path, new: str) -> None:
    tj = d / "transcript.json"
    if tj.exists():
        data = json.loads(tj.read_text())
        data["call_id"] = new
        tj.write_text(json.dumps(data, indent=2))
    tt = d / "transcript.txt"
    if tt.exists():
        tt.write_text(re.sub(r"^Call: .*$", f"Call: {new}", tt.read_text(), count=1, flags=re.M))
    fm = d / "findings.md"
    if fm.exists():
        fm.write_text(re.sub(r"^## .*$", f"## {new}", fm.read_text(), count=1, flags=re.M))


if __name__ == "__main__":
    main()
