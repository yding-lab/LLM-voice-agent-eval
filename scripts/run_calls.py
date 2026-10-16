"""Single entry point: starts the caller worker, places calls one at a time, and
waits for each to finish (transcript + recording written) before the next.

    python scripts/run_calls.py --list
    python scripts/run_calls.py schedule_new_checkup
    python scripts/run_calls.py --all
    python scripts/run_calls.py refill_routine barge_in --repeat 2

Each call lands in calls/NN-<scenario>/ with recording.ogg (stereo: clinic left,
patient right), recording.mp3 (mono mix), transcript.txt/.json and scenario.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livekit import api  # noqa: E402

from caller.config import AGENT_NAME, CALLS_DIR, TARGET_NUMBER  # noqa: E402
from caller.persona import load_scenarios  # noqa: E402

CALL_TIMEOUT_S = 8 * 60
GAP_BETWEEN_CALLS_S = 8


class Worker:
    """Runs `python -m caller.agent start` and waits until it has registered."""

    def __init__(self, log_path: Path) -> None:
        self.ready = threading.Event()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_path.open("w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "caller.agent", "start"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._log.write(line)
            self._log.flush()
            if "registered worker" in line:
                self.ready.set()
            if any(k in line for k in ("ERROR", "error", "patient-caller", "call ")):
                print("  [worker]", line.rstrip()[:240])

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()


MIN_DURATION_S = 45
MIN_TURNS_EACH_SIDE = 3


def check_call(out_dir: Path) -> list[str]:
    """Reasons this call doesn't count as a full conversation (empty list = valid)."""
    problems = []
    for name in ("recording.mp3", "recording.ogg", "transcript.txt"):
        if not (out_dir / name).exists():
            problems.append(f"missing {name}")
    tj = out_dir / "transcript.json"
    if not tj.exists():
        return problems + ["missing transcript.json"]
    data = json.loads(tj.read_text())
    if data.get("end_reason", "").startswith("dial failed"):
        problems.append(data["end_reason"])
    if data.get("duration_s", 0) < MIN_DURATION_S:
        problems.append(f"only {data.get('duration_s', 0):.0f}s long")
    for speaker in ("CLINIC AGENT", "PATIENT (bot)"):
        n = sum(1 for t in data.get("turns", []) if t["speaker"] == speaker)
        if n < MIN_TURNS_EACH_SIDE:
            problems.append(f"{speaker} spoke only {n} time(s)")
    return problems


def write_index() -> int:
    """Rewrites calls/INDEX.md (one row per call) and returns how many calls are valid."""
    rows, valid = [], 0
    for d in sorted(p for p in CALLS_DIR.iterdir() if p.is_dir()):
        tj = d / "transcript.json"
        data = json.loads(tj.read_text()) if tj.exists() else {}
        problems = check_call(d)
        valid += not problems
        lat = data.get("latency", {})
        rows.append(
            f"| {d.name} | {data.get('duration_s', 0):.0f}s | {data.get('end_reason', '?')} | "
            f"{lat.get('median_s', '-')} | {'yes' if not problems else 'NO: ' + '; '.join(problems)} |"
        )
    (CALLS_DIR / "INDEX.md").write_text(
        "# Calls\n\nValid = recording + transcript saved, at least "
        f"{MIN_DURATION_S}s, and at least {MIN_TURNS_EACH_SIDE} turns from each side.\n\n"
        "| Call | Length | Ended because | Bot median latency (s) | Valid |\n"
        "|---|---|---|---|---|\n" + "\n".join(rows) + f"\n\n**{valid} valid calls.**\n"
    )
    return valid


def next_call_number() -> int:
    nums = [int(m.group(1)) for p in CALLS_DIR.glob("*") if (m := re.match(r"(\d+)-", p.name))]
    return max(nums, default=0) + 1


async def place_call(lk: api.LiveKitAPI, scenario_id: str, out_dir: Path) -> None:
    room = f"call-{out_dir.name}-{uuid.uuid4().hex[:6]}"
    await lk.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=60, departure_timeout=10))
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME,
            room=room,
            metadata=json.dumps(
                {"scenario": scenario_id, "output_dir": str(out_dir), "phone_number": TARGET_NUMBER}
            ),
        )
    )


async def run(scenario_ids: list[str], worker: Worker | None) -> None:
    async with api.LiveKitAPI() as lk:
        for i, sid in enumerate(scenario_ids):
            out_dir = CALLS_DIR / f"{next_call_number():02d}-{sid}"
            out_dir.mkdir(parents=True)
            print(f"\n=== Call {out_dir.name} ({i + 1}/{len(scenario_ids)}) -> {TARGET_NUMBER}")
            await place_call(lk, sid, out_dir)

            started = time.time()
            while not (out_dir / "DONE").exists():
                if worker and not worker.alive():
                    sys.exit("Worker exited unexpectedly; see logs/worker.log")
                if time.time() - started > CALL_TIMEOUT_S:
                    print("  timed out waiting for the call to finish; moving on")
                    break
                await asyncio.sleep(2)

            done = out_dir / "DONE"
            reason = done.read_text().strip() if done.exists() else "timeout"
            print(f"  finished in {time.time() - started:.0f}s: {reason}")
            problems = check_call(out_dir)
            print("  VALID full conversation" if not problems else f"  NOT VALID: {'; '.join(problems)}")
            if i < len(scenario_ids) - 1:
                await asyncio.sleep(GAP_BETWEEN_CALLS_S)
    print(f"\n{write_index()} valid calls so far (see calls/INDEX.md)")


def main() -> None:
    scenarios = load_scenarios()
    parser = argparse.ArgumentParser(description="Place test calls to the clinic agent.")
    parser.add_argument("scenarios", nargs="*", help="scenario ids (see --list)")
    parser.add_argument("--all", action="store_true", help="run every scenario in file order")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--check", action="store_true", help="re-check saved calls, rewrite calls/INDEX.md")
    parser.add_argument("--repeat", type=int, default=1, help="run each scenario N times")
    parser.add_argument(
        "--no-worker", action="store_true",
        help="don't start a worker (use one you started yourself, e.g. `python -m caller.agent dev`)",
    )
    args = parser.parse_args()

    if args.list:
        for sc in scenarios.values():
            print(f"{sc.id:28s} {sc.title}")
        return
    if args.check:
        print(f"{write_index()} valid calls; details in calls/INDEX.md")
        return

    ids = list(scenarios) if args.all else args.scenarios
    unknown = [s for s in ids if s not in scenarios]
    if not ids or unknown:
        parser.error(f"unknown or missing scenario(s): {unknown or 'none given'}; try --list")
    ids = [s for s in ids for _ in range(args.repeat)]

    worker = None
    if not args.no_worker:
        print("Starting caller worker...")
        worker = Worker(ROOT / "logs" / "worker.log")
        if not worker.ready.wait(timeout=90):
            worker.stop()
            sys.exit("Worker did not register within 90s; see logs/worker.log")
        print("Worker registered.")
    try:
        asyncio.run(run(ids, worker))
    finally:
        if worker:
            worker.stop()


if __name__ == "__main__":
    main()
