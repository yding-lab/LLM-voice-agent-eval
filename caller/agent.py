"""The patient caller: a LiveKit Agents worker that dials the clinic's test line
and plays a scenario-driven patient using a STT -> LLM -> TTS pipeline.

Run via scripts/run_calls.py (which starts this worker and dispatches calls),
or manually:  python -m caller.agent dev
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from livekit import api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    inference,
    llm,
    room_io,
)
from livekit.plugins import cartesia, deepgram, openai, silero

from .audio import export_recording
from .config import AGENT_NAME, ROOT, TARGET_NUMBER, load_settings, prepare_console_env
from .persona import Scenario, build_instructions, load_scenarios
from .transcript import CallLog

logger = logging.getLogger("llm-voice-agent-eval")

CLINIC_IDENTITY = "clinic-line"
HELLO_AFTER_S = 6.0  # if the clinic hasn't spoken this long after answering, say "hello?"
HARD_LIMIT_S = 6 * 60  # absolute cap on call length, whatever the scenario says


# "This call may be recorded for quality and training purposes." Clinics play a notice like
# this, pause, and only then greet. It is a complete sentence followed by silence, so the
# turn detector rightly ends the turn, and the LLM used to answer it as if it were a greeting.
RECORDING_NOTICE = re.compile(
    r"(this\s+)?call\s+(may|might|will|can|is going to)\s+be\s+(recorded|monitored)[^.?!]*[.?!]?",
    re.IGNORECASE,
)


def is_only_recording_notice(text: str) -> bool:
    """True when a clinic turn is just the recording notice, with no greeting or question."""
    if not RECORDING_NOTICE.search(text):
        return False
    rest = RECORDING_NOTICE.sub(" ", text)
    return len(re.findall(r"[A-Za-z']+", rest)) <= 2  # allow a stray "okay" or "and"


def should_wait_for_greeting(chat_ctx: llm.ChatContext) -> bool:
    """True while the patient hasn't spoken yet and the clinic's latest turn is only the
    recording notice. Read from the conversation itself, so it needs no extra state."""
    messages = [m for m in chat_ctx.items if isinstance(m, llm.ChatMessage)]
    if any(m.role == "assistant" and (m.text_content or "").strip() for m in messages):
        return False  # the call is under way; never second-guess later turns
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    return last_user is not None and is_only_recording_notice(last_user.text_content or "")


GOODBYE = re.compile(r"\b(good ?bye|bye|take care|have a (good|great|nice) (day|night|evening))\b", re.I)
TRANSFER_OR_HOLD = re.compile(r"\b(transferr?(ing)?|connect(ing)? you|one moment|hold on|please hold)\b", re.I)
MAX_END_CALL_REFUSALS = 2  # after this, trust the LLM; the watchdog still caps call length


def end_call_blocker(chat_ctx: llm.ChatContext) -> str | None:
    """Why hanging up now would be wrong, or None if it's fine.

    Seen in early calls: the bot hung up right after asking a question, and said goodbye
    while being transferred."""
    messages = [
        m for m in chat_ctx.items
        if isinstance(m, llm.ChatMessage) and m.role in ("user", "assistant") and (m.text_content or "").strip()
    ]
    if not messages:
        return None
    last = messages[-1]
    text = (last.text_content or "").strip()
    if last.role == "user":
        # The clinic spoke last. Fine if it said goodbye; not if it's putting us on hold.
        if TRANSFER_OR_HOLD.search(text) and not GOODBYE.search(text):
            return ("Don't hang up: the receptionist is transferring you or asked you to hold. "
                    "Call hold_silently and wait for the next person.")
        return None
    if text.endswith("?"):
        return ("Don't hang up: you just asked a question. Call hold_silently and wait for "
                "the answer, then say goodbye before ending the call.")
    if not GOODBYE.search(text):
        return "Don't hang up yet: say a short goodbye first, then call end_call."
    return None


class PatientAgent(Agent):
    def __init__(self, instructions: str, hangup) -> None:
        super().__init__(instructions=instructions)
        self._hangup = hangup
        self._end_call_refusals = 0

    async def llm_node(self, chat_ctx, tools, model_settings):
        # llm_node runs right before every reply, for spoken turns (phone, console) and for
        # typed ones (console --text). Skipping it here means: no LLM call, no speech.
        # Only the opening is affected. A notice and greeting heard as one turn is answered.
        if should_wait_for_greeting(chat_ctx):
            logger.info("recording notice heard; staying silent until the clinic greets us")
            return
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk

    @function_tool
    async def hold_silently(self, context: RunContext) -> None:
        """Say nothing and keep listening. Use when the receptionist is checking or looking
        something up, asked you to hold, is transferring you, or only gave a short
        acknowledgement and is clearly about to continue."""
        return None  # returning None means no spoken reply follows

    @function_tool
    async def end_call(self, context: RunContext) -> str | None:
        """Hang up the phone. Only use this after you have already said goodbye."""
        await context.wait_for_playout()  # also commits what we just said to the history
        reason = end_call_blocker(context.session.history)
        if reason and self._end_call_refusals < MAX_END_CALL_REFUSALS:
            self._end_call_refusals += 1
            logger.info("end_call refused: %s", reason)
            return reason  # goes back to the LLM instead of hanging up
        await asyncio.sleep(0.8)  # let the last syllable reach the phone line
        await self._hangup("patient bot said goodbye")
        return None


# Console works without LiveKit Cloud settings. AgentServer() reads LIVEKIT_URL when it is
# created, so a half-filled LiveKit config has to be cleared before this line.
if "console" in sys.argv[1:]:
    prepare_console_env()

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    # No metadata means a local rehearsal (`python -m caller.agent console`): you play the
    # receptionist through your mic, nothing is dialed. Pick the scenario with SCENARIO=...
    rehearsal = "scenario" not in meta
    # Console needs only provider keys; real calls validate SIP + patient settings strictly.
    settings = load_settings(outbound=not rehearsal)
    if settings.using_default_patient:
        logger.info("console mode: using the fictional default patient (PATIENT_* not set)")
    scenarios = load_scenarios(settings)
    scenario_id = meta.get("scenario") or os.getenv("SCENARIO", "schedule_new_checkup")
    if scenario_id not in scenarios:
        raise RuntimeError(f"Unknown scenario {scenario_id!r}. Valid ids: {', '.join(scenarios)}")
    scenario: Scenario = scenarios[scenario_id]
    out_dir = Path(meta.get("output_dir") or ROOT / "logs" / f"rehearsal-{int(time.time())}")
    call_to = meta.get("phone_number", TARGET_NUMBER)

    # Safety rail: this bot only ever calls the configured target number.
    if not TARGET_NUMBER or call_to != TARGET_NUMBER:
        logger.error("refusing to dial %s: only %s is allowed", call_to, TARGET_NUMBER)
        ctx.shutdown("disallowed number")
        return

    log = CallLog(call_id=out_dir.name, scenario_id=scenario.id, scenario_title=scenario.title)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(
            model=settings.stt_model,
            language="en-US",
            smart_format=True,  # dates, times and phone numbers come back formatted
            keyterm=[
                scenario.identity.get("first", settings.patient_first_name),
                scenario.identity.get("last", settings.patient_last_name),
            ],
        ),
        llm=openai.LLM(model=settings.llm_model, temperature=0.8, parallel_tool_calls=False),
        tts=cartesia.TTS(model=settings.tts_model, voice=scenario.tts_voice or settings.tts_voice),
        turn_handling={
            # Semantic end-of-turn model on top of VAD: waits through mid-sentence pauses
            # ("let me check... okay, I have Tuesday at...") instead of jumping in.
            "turn_detection": inference.TurnDetector(),
            "endpointing": {
                "min_delay": settings.min_endpointing_delay,
                "max_delay": settings.max_endpointing_delay,
            },
            # The clinic can interrupt us (that's realistic), but a cough or "mm-hmm"
            # shouldn't, and a false interruption resumes our sentence.
            "interruption": {
                "min_duration": 0.6,
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.5,
            },
            # Start the LLM on the interim transcript to shave latency off each turn.
            "preemptive_generation": {"enabled": True},
        },
    )
    log.attach(session)

    async def hangup(reason: str) -> None:
        if log.end_reason == "unknown":
            log.end_reason = reason
        log.event(f"hangup: {reason}")
        try:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
        except Exception:  # room may already be gone
            pass
        ctx.shutdown(reason)

    async def finalize() -> None:
        try:
            await session.aclose()  # flushes the recorder so audio.ogg is complete
        except Exception:
            logger.exception("error closing session")
        log.write(out_dir)
        src = ctx.session_directory / "audio.ogg"
        if src.exists():
            try:
                export_recording(src, out_dir)
            except Exception:
                logger.exception("failed to export recording")
        elif not rehearsal:  # console mode doesn't record; real calls always should
            logger.warning("no recording found at %s", src)
        (out_dir / "scenario.json").write_text(
            json.dumps(
                {"id": scenario.id, "title": scenario.title, "goal": scenario.goal,
                 "try": scenario.try_, "expected": scenario.expected,
                 "pass_outcome": scenario.pass_outcome,
                 "checks": [vars(c) for c in scenario.checks]},
                indent=2,
            )
        )
        (out_dir / "DONE").write_text(log.end_reason + "\n")
        logger.info("call %s finished (%s) -> %s", log.call_id, log.end_reason, out_dir)

    ctx.add_shutdown_callback(finalize)

    @session.on("close")
    def _on_close(ev) -> None:
        if log.end_reason == "unknown":
            reason = getattr(ev.reason, "value", ev.reason)
            log.end_reason = (
                "clinic hung up" if reason == "participant_disconnected" else f"session closed: {reason}"
            )
        ctx.shutdown("session closed")

    # --- scripted barge-in (only for scenarios that ask for it) ---------------------
    heard_clinic = asyncio.Event()
    clinic_turns = 0
    barge_target = scenario.interrupt.on_clinic_turn if scenario.interrupt else None

    async def barge_in() -> None:
        nonlocal barge_target
        assert scenario.interrupt is not None
        await asyncio.sleep(scenario.interrupt.after_seconds)
        if session.user_state == "speaking":
            log.event("scripted barge-in: talking over the clinic agent")
            session.say(scenario.interrupt.line, allow_interruptions=False)
            barge_target = None
        else:  # that turn was too short to talk over; try the next one
            barge_target = clinic_turns + 1

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        nonlocal clinic_turns
        if ev.new_state != "speaking":
            return
        heard_clinic.set()
        clinic_turns += 1
        if barge_target is not None and clinic_turns == barge_target:
            asyncio.create_task(barge_in())

    # --- start the session, then dial ----------------------------------------------
    patient = PatientAgent(build_instructions(scenario, settings), hangup)
    if rehearsal:
        log.t0 = time.time()
        await session.start(agent=patient, room=ctx.room)
        return

    await ctx.connect()
    log.t0 = time.time()  # recording starts with the session; transcript times match it
    session_started = asyncio.create_task(
        session.start(
            agent=patient,
            room=ctx.room,
            room_options=room_io.RoomOptions(participant_identity=CLINIC_IDENTITY),
            record=True,  # stereo OGG of both sides, written to ctx.session_directory
        )
    )

    log.event(f"dialing {call_to}")
    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=settings.sip_trunk_id,
                sip_call_to=call_to,
                participant_identity=CLINIC_IDENTITY,
                participant_name="Clinic agent",
                wait_until_answered=True,
            )
        )
    except api.TwirpError as e:
        log.end_reason = f"dial failed: {e.message} (SIP {e.metadata.get('sip_status_code')})"
        logger.error(log.end_reason)
        ctx.shutdown(log.end_reason)
        return

    await session_started
    log.event("call answered")

    # Let the clinic greet first, like a real caller would. Only nudge if it stays silent.
    try:
        await asyncio.wait_for(heard_clinic.wait(), timeout=HELLO_AFTER_S)
    except asyncio.TimeoutError:
        log.event("clinic silent after answer; saying hello")
        session.say("Hi, hello?")

    # Soft limit: ask the bot to wrap up. Hard limit: hang up regardless.
    async def watchdog() -> None:
        await asyncio.sleep(scenario.max_minutes * 60 + 30)
        log.event("soft time limit: asking the patient to wrap up")
        session.generate_reply(
            instructions="You need to go now. Politely say you have to run, say goodbye in "
            "one short sentence, then call end_call."
        )
        await asyncio.sleep(max(45.0, HARD_LIMIT_S - scenario.max_minutes * 60 - 30))
        await hangup("hard time limit")

    asyncio.create_task(watchdog())


if __name__ == "__main__":
    cli.run_app(server)
