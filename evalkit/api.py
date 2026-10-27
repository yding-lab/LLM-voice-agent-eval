"""HTTP API for the evaluation pipeline.

    uvicorn evalkit.api:app          # or: python -m evalkit serve, or: docker run -p 8000:8000 ...
    open http://localhost:8000/docs  # interactive docs: try every endpoint from the browser

The judge provider is fixed by the server's configuration (ANALYSIS_PROVIDER), never by the
request, so a public demo can't be used to spend someone's API credits. The Docker image
defaults to `replay`: real recorded judge output for the bundled sample calls, no key needed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import __version__
from .compare import compare
from .config import EvalConfig
from .data import Call, common_checks, iter_calls, known_names, scenarios
from .judge import judge_call
from .paths import CALLS_DIR, RUNS_DIR
from .providers import ReplayProvider, make_provider
from .report import findings_md
from .runs import list_runs, load_run, price
from .scoring import as_dict, load_labels, score, score_by_split

log = logging.getLogger("evalkit.api")

app = FastAPI(
    title="llm-voice-agent-eval",
    version=__version__,
    description="Evaluate voice-agent call transcripts with a measured LLM judge: pseudonymized input, "
                "per-check verdicts with evidence, scores against human labels, and tracked runs.",
)


@lru_cache
def settings() -> EvalConfig:
    return EvalConfig.load()


@lru_cache
def provider():
    return make_provider(settings())


@lru_cache
def sample_calls() -> dict[str, Call]:
    return {c.name: c for c in iter_calls(CALLS_DIR)}


# ---------------------------------------------------------------- models
class Check(BaseModel):
    id: str = Field(examples=["SCH-01"])
    fail_if: str = Field(examples=["Confirms an appointment without checking availability"])
    severity: str = Field("Medium", examples=["Critical", "High", "Medium", "Low"])


class Scenario(BaseModel):
    title: str
    goal: str = ""
    expected: str = ""
    pass_outcome: str = ""
    checks: list[Check] = []


class EvaluateRequest(BaseModel):
    transcript: str = Field(description="Call transcript, one turn per line, e.g. "
                                        "'[00:12] CLINIC AGENT: ...' / '[00:15] PATIENT (bot): ...'")
    scenario_id: str | None = Field(None, description="A scenario from GET /scenarios")
    scenario: Scenario | None = Field(None, description="Or define the scenario and its checks inline")
    call_time: datetime | None = Field(None, description="When the call happened (lets the judge check 'upcoming')")


class JudgeInfo(BaseModel):
    provider: str
    model: str
    rubric: str
    redacted: bool


class EvaluateResponse(BaseModel):
    judge: JudgeInfo
    summary: str | None
    pass_outcome_met: bool | None
    counts: dict[str, int]
    checks: list[dict]
    other_findings: list[dict]
    redactions: dict[str, int]
    latency_s: float | None
    tokens: dict[str, int | None]
    cost_usd: float | None
    report_markdown: str
    human_label_score: dict | None = Field(None, description="Present when the transcript is a labelled sample call")


# ---------------------------------------------------------------- helpers
def _evaluate(call: Call) -> EvaluateResponse:
    cfg, prov = settings(), provider()
    if isinstance(prov, ReplayProvider):
        if not prov.has(call.input_sha):
            raise HTTPException(422, "This server runs in replay mode (no API key): it can only return the "
                                     "recorded judge output for the bundled sample calls (GET /samples). "
                                     "Set ANALYSIS_PROVIDER=openai or bedrock to judge new transcripts.")
        info = JudgeInfo(provider="replay", model=prov.model, rubric=prov.recorded_rubric or "?", redacted=False)
    else:
        info = JudgeInfo(provider=prov.name, model=prov.model, rubric=cfg.rubric, redacted=cfg.redact)
    out = judge_call(prov, call, cfg, known_names())
    if out.status != "ok":
        raise HTTPException(502, f"judge failed after {out.attempts} attempts: {out.error}")
    r = out.output
    counts = {"fail": 0, "pass": 0, "not_tested": 0}
    for c in r.get("checks", []):
        counts[c.get("result", "not_tested")] = counts.get(c.get("result", "not_tested"), 0) + 1
    human = None
    if call.name in sample_calls() and sample_calls()[call.name].input_sha == call.input_sha:
        s = score({call.name: r}, load_labels(), "sample", [call.name])
        human = {"failed_checks_by_human_label": dict(s.fail_labels), "known_bugs": s.bugs}
    return EvaluateResponse(
        judge=info, summary=r.get("summary"), pass_outcome_met=r.get("pass_outcome_met"), counts=counts,
        checks=r.get("checks", []), other_findings=r.get("other_findings", []), redactions=out.redactions,
        latency_s=out.latency_s, tokens={"input": out.input_tokens, "output": out.output_tokens},
        cost_usd=0.0 if info.provider == "replay" else price(cfg.model, out.input_tokens, out.output_tokens),
        report_markdown=findings_md(call.name, r), human_label_score=human)


# ---------------------------------------------------------------- routes
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health", tags=["service"])
def health() -> dict:
    cfg, prov = settings(), provider()
    return {"status": "ok", "version": __version__, "provider": prov.name, "model": prov.model,
            "rubric": getattr(prov, "recorded_rubric", None) or cfg.rubric, "sample_calls": len(sample_calls())}


@app.get("/scenarios", tags=["inputs"])
def list_scenarios() -> list[dict]:
    """Scenarios a transcript can be graded against (plus the common checks applied to every call)."""
    return [{"id": sid, "title": s.get("title"), "checks": len(s.get("checks", [])) + len(common_checks())}
            for sid, s in scenarios().items()]


@app.get("/samples", tags=["inputs"])
def list_samples() -> list[dict]:
    """Recorded calls bundled with the service. In replay mode these are the transcripts you can evaluate."""
    return [{"call": c.name, "scenario": c.scenario.get("title"), "duration_s": c.meta.get("duration_s"),
             "transcript_chars": len(c.transcript)} for c in sample_calls().values()]


@app.get("/samples/{call}", tags=["inputs"])
def get_sample(call: str) -> dict:
    c = sample_calls().get(call) or _404(call)
    return {"call": c.name, "scenario": c.scenario, "started_at": c.started_at, "transcript": c.transcript}


@app.post("/samples/{call}/evaluate", response_model=EvaluateResponse, tags=["evaluate"])
def evaluate_sample(call: str) -> EvaluateResponse:
    """Evaluate a bundled call. Works in replay mode with no API key."""
    return _evaluate(sample_calls().get(call) or _404(call))


@app.post("/evaluate", response_model=EvaluateResponse, tags=["evaluate"])
def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    """Evaluate a transcript against a named scenario or inline checks.

    In replay mode only the bundled sample transcripts can be evaluated (paste one from
    GET /samples/{call}); with a live provider, any transcript works."""
    if req.scenario is not None:
        sc = req.scenario.model_dump()
    elif req.scenario_id:
        sc = scenarios().get(req.scenario_id) or _404(req.scenario_id)
    else:
        match = next((c for c in sample_calls().values() if c.transcript == req.transcript), None)
        if not match:
            raise HTTPException(422, "give scenario_id or scenario")
        sc = match.scenario
    name = next((c.name for c in sample_calls().values() if c.transcript == req.transcript), "submitted")
    started = req.call_time.timestamp() if req.call_time else (
        sample_calls()[name].started_at if name in sample_calls() else None)
    return _evaluate(Call(name=name, transcript=req.transcript, scenario=sc, started_at=started))


@app.get("/runs", tags=["experiments"])
def runs() -> list[dict]:
    """Tracked judge runs (evals/runs/*/manifest.json)."""
    return list_runs(RUNS_DIR)


@app.get("/runs/{run}", tags=["experiments"])
def run_detail(run: str) -> dict:
    path = RUNS_DIR / run
    if not (path / "manifest.json").exists() or path.resolve().parent != RUNS_DIR.resolve():
        _404(run)
    outputs, manifest = load_run(path)
    return {"manifest": manifest, "scores": as_dict(score_by_split(outputs))}


@app.get("/compare", tags=["experiments"])
def compare_runs(before: str, after: str) -> dict:
    """Setup changes, score deltas per split, cost and latency, and check-by-check differences."""
    paths = [RUNS_DIR / r for r in (before, after)]
    for p, r in zip(paths, (before, after)):
        if not (p / "manifest.json").exists() or p.resolve().parent != RUNS_DIR.resolve():
            _404(r)
    return compare(*paths)


def _404(what: str):
    raise HTTPException(404, f"not found: {what}")


__all__ = ["app", "Path"]
