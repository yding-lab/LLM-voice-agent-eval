"""Checks everything a real call needs, without placing a call.

    python scripts/preflight.py            # full check (uses each API once, costs ~nothing)
    python scripts/preflight.py --offline  # skip the API key checks

Each line prints OK or FAIL with a hint. Fix the first FAIL and run it again.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[bool] = []


def report(name: str, ok: bool, detail: str = "") -> bool:
    results.append(ok)
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  ->  {detail}" if detail else ""))
    return ok


def check(name: str, hint: str = ""):
    """Decorator: run fn(); exception or False -> FAIL with the hint."""

    def wrap(fn):
        try:
            out = fn()
            if asyncio.iscoroutine(out):
                out = asyncio.run(out)
            if isinstance(out, tuple):
                ok, detail = out
            elif isinstance(out, bool):
                ok, detail = out, ""
            else:
                ok, detail = True, out or ""
            report(name, ok, detail if ok else (detail or hint))
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}".strip()[:200]
            report(name, False, f"{msg}" + (f"  |  {hint}" if hint else ""))
            if os.getenv("PREFLIGHT_DEBUG"):
                traceback.print_exc()
        return fn

    return wrap


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="skip API key checks")
    parser.add_argument("--console", action="store_true",
                        help="only check what console rehearsal needs (no LiveKit/SIP/patient settings)")
    args = parser.parse_args()

    print("Local setup")

    @check("Python 3.11-3.13", "install a newer Python: brew install python@3.12, then rerun ./setup.sh")
    def _():
        v = sys.version_info
        return ((3, 11) <= v < (3, 14), f"Python {v.major}.{v.minor}")

    @check("Packages installed", "activate your venv (source .venv/bin/activate) and run ./setup.sh")
    def _():
        import livekit.agents  # noqa: F401
        import yaml  # noqa: F401
        from livekit.plugins import cartesia, deepgram, openai, silero  # noqa: F401

        return f"livekit-agents {livekit.agents.__version__}"

    @check("MP3 + Opus encoders available", "pip install --force-reinstall av")
    def _():
        import av

        missing = [c for c in ("libmp3lame", "libopus") if c not in av.codecs_available]
        return (not missing, f"missing: {missing}" if missing else "")

    @check(".env file present", "cp .env.example .env, then fill it in")
    def _():
        return (ROOT / ".env").exists()

    from caller import config  # loads .env

    @check("Console settings (provider keys)")
    def _():
        bad = config.missing_settings(outbound=False)
        return (not bad, f"still empty or placeholder: {', '.join(bad)}" if bad else "")

    if not args.console:

        @check("Real-call settings (LiveKit, SIP trunk, caller id, test patient)")
        def _():
            bad = [k for k in config.missing_settings(outbound=True) if k not in config.PROVIDER_KEYS]
            if not os.getenv("CALLER_ID_NUMBER", "").startswith("+") or "X" in os.getenv("CALLER_ID_NUMBER", ""):
                bad.append("CALLER_ID_NUMBER")
            return (not bad, f"still empty or placeholder: {', '.join(bad)}" if bad else "")

    @check("Scenarios load and prompts build", "check caller/scenarios.yaml")
    def _():
        from caller.persona import build_instructions, load_scenarios

        d = config.CONSOLE_PATIENT_DEFAULTS
        s = config.Settings(
            outbound=False, sip_trunk_id="", llm_model="-", stt_model="-", tts_model="-", tts_voice="-",
            patient_first_name=d["PATIENT_FIRST_NAME"], patient_last_name=d["PATIENT_LAST_NAME"],
            patient_dob=d["PATIENT_DOB"], patient_phone=d["PATIENT_PHONE"],
            min_endpointing_delay=0.7, max_endpointing_delay=3.0,
        )
        sc = load_scenarios(s)
        for one in sc.values():
            build_instructions(one, s)
        return f"{len(sc)} scenarios"

    @check("Target number is set (E.164)", "set TARGET_NUMBER in .env, e.g. +14155550100")
    def _():
        return (bool(config.E164.match(config.TARGET_NUMBER)), config.TARGET_NUMBER or "not set")

    @check("Voice activity model loads", "run: python -m livekit.agents download-files")
    def _():
        from livekit.plugins import silero

        silero.VAD.load()

    @check("Recording export (stereo OGG -> MP3) works")
    def _():
        import av
        import numpy as np

        from caller.audio import export_recording

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "audio.ogg"
            rate, n = 48000, 48000 * 2
            with av.open(str(src), "w", format="ogg") as c:
                st = c.add_stream("libopus", rate=rate, layout="stereo")
                tone = (0.2 * np.sin(np.arange(n) * 2 * np.pi * 440 / rate)).astype(np.float32)
                for i in range(0, n, 960):
                    block = np.stack([tone[i:i + 960], tone[i:i + 960]])
                    fr = av.AudioFrame.from_ndarray(block, format="fltp", layout="stereo")
                    fr.sample_rate = rate
                    for p in st.encode(fr):
                        c.mux(p)
                for p in st.encode(None):
                    c.mux(p)
            out = export_recording(src, Path(tmp) / "out")
            return all(p.stat().st_size > 1000 for p in out)

    if args.offline:
        return finish()

    print("\nAPI keys and telephony")
    import aiohttp

    async def http_get(url: str, headers: dict) -> tuple[int, dict]:
        async with aiohttp.ClientSession() as http:
            async with http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                body = await r.json(content_type=None) if r.status == 200 else {}
                return r.status, body

    @check("OpenAI key + model", "check OPENAI_API_KEY and billing at platform.openai.com")
    def _():
        from openai import OpenAI

        model = os.getenv("LLM_MODEL", "gpt-4.1-mini")
        OpenAI().models.retrieve(model)
        return model

    @check("Deepgram key", "check DEEPGRAM_API_KEY at console.deepgram.com")
    async def _():
        status, _ = await http_get("https://api.deepgram.com/v1/projects",
                                   {"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"})
        return (status == 200, f"HTTP {status}")

    @check("Cartesia key + voice", "check CARTESIA_API_KEY / TTS_VOICE_ID at play.cartesia.ai")
    async def _():
        voice = os.getenv("TTS_VOICE_ID", "f786b574-daa5-4673-aa0c-cbe3e8534c02")
        status, body = await http_get(f"https://api.cartesia.ai/voices/{voice}",
                                      {"X-API-Key": os.environ["CARTESIA_API_KEY"],
                                       "Cartesia-Version": "2025-04-16"})
        return (status == 200, body.get("name", "") if status == 200 else f"HTTP {status}")

    if args.console:
        return finish()

    @check("LiveKit credentials + outbound SIP trunk",
           "check LIVEKIT_* values; run python scripts/setup_trunk.py --list to see trunk ids")
    async def _():
        from livekit import api

        async with api.LiveKitAPI() as lk:
            await lk.room.list_rooms(api.ListRoomsRequest())
            trunks = (await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())).items
        want = os.environ["SIP_OUTBOUND_TRUNK_ID"]
        match = [t for t in trunks if t.sip_trunk_id == want]
        if not match:
            return (False, f"trunk {want} not found; existing: {[t.sip_trunk_id for t in trunks]}")
        t = match[0]
        caller_id = os.getenv("CALLER_ID_NUMBER", "")
        if caller_id not in t.numbers:
            return (False, f"CALLER_ID_NUMBER {caller_id} is not on the trunk (has {list(t.numbers)})")
        return f"{t.address}, caller id {caller_id}"

    finish()


def finish() -> None:
    failed = results.count(False)
    print()
    if failed:
        print(f"{failed} check(s) failed. Fix the first FAIL above and run this again.")
        sys.exit(1)
    print("All checks passed. Next: SCENARIO=schedule_new_checkup python -m caller.agent console")


if __name__ == "__main__":
    main()
