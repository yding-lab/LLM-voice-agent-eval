"""Collects both sides of the conversation with timestamps that line up with the
recording, plus per-turn latency of our own bot, and writes them to disk."""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from livekit.agents import AgentSession
from livekit.agents.llm import ChatMessage

# In our session the clinic agent is the "user" and our patient bot is the "assistant".
SPEAKER = {"user": "CLINIC AGENT", "assistant": "PATIENT (bot)"}


@dataclass
class Turn:
    t: float  # seconds from recording start
    speaker: str
    text: str
    interrupted: bool = False
    latency: float | None = None  # bot only: clinic stopped speaking -> bot started speaking


@dataclass
class CallLog:
    call_id: str
    scenario_id: str
    scenario_title: str
    t0: float = field(default_factory=time.time)
    turns: list[Turn] = field(default_factory=list)
    events: list[tuple[float, str]] = field(default_factory=list)
    end_reason: str = "unknown"
    answered_at: float | None = None

    # -- wiring ---------------------------------------------------------------
    def attach(self, session: AgentSession) -> None:
        @session.on("conversation_item_added")
        def _on_item(ev) -> None:
            item = ev.item
            if not isinstance(item, ChatMessage) or item.role not in SPEAKER:
                return
            text = (item.text_content or "").strip()
            if not text:
                return
            metrics = item.metrics or {}
            started = metrics.get("started_speaking_at") or item.created_at
            self.turns.append(
                Turn(
                    t=max(0.0, started - self.t0),
                    speaker=SPEAKER[item.role],
                    text=text,
                    interrupted=bool(item.interrupted),
                    latency=metrics.get("e2e_latency") if item.role == "assistant" else None,
                )
            )

        @session.on("function_tools_executed")
        def _on_tools(ev) -> None:
            for call in ev.function_calls:
                if call.name != "hold_silently":
                    self.event(f"tool: {call.name}")

    def event(self, text: str) -> None:
        self.events.append((time.time() - self.t0, text))

    # -- output ---------------------------------------------------------------
    def latency_stats(self) -> dict[str, float]:
        lat = [t.latency for t in self.turns if t.latency is not None]
        if not lat:
            return {}
        return {
            "bot_turns": len(lat),
            "median_s": round(statistics.median(lat), 2),
            "p90_s": round(sorted(lat)[math.ceil(0.9 * len(lat)) - 1], 2),  # nearest rank
            "max_s": round(max(lat), 2),
        }

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        duration = time.time() - self.t0
        rows: list[tuple[float, str]] = []
        for turn in self.turns:
            mark = "  [cut off]" if turn.interrupted else ""
            rows.append((turn.t, f"[{_mmss(turn.t)}] {turn.speaker}: {turn.text}{mark}"))
        for t, text in self.events:
            rows.append((t, f"[{_mmss(t)}] -- {text} --"))
        rows.sort(key=lambda r: r[0])

        header = [
            f"Call: {self.call_id}",
            f"Scenario: {self.scenario_id} - {self.scenario_title}",
            f"Date: {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.t0))}",
            f"Duration: {_mmss(duration)}   End: {self.end_reason}",
            "Timestamps are relative to the start of recording.ogg / recording.mp3.",
            "Clinic-side text comes from our speech-to-text; check the audio before quoting it.",
            "",
        ]
        (out_dir / "transcript.txt").write_text("\n".join(header + [r[1] for r in rows]) + "\n")

        (out_dir / "transcript.json").write_text(
            json.dumps(
                {
                    "call_id": self.call_id,
                    "scenario": self.scenario_id,
                    "started_at": self.t0,
                    "duration_s": round(duration, 1),
                    "end_reason": self.end_reason,
                    "latency": self.latency_stats(),
                    "turns": [asdict(t) for t in self.turns],
                    "events": self.events,
                },
                indent=2,
            )
        )


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
