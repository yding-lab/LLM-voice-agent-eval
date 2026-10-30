"""Resuming a run: saved outputs are reused only when inputs and settings are provably unchanged."""

import json
import shutil
from dataclasses import replace

import pytest

from evalkit.config import EvalConfig
from evalkit.data import iter_calls
from evalkit.paths import RUNS_DIR
from evalkit.pipeline import ResumeConflict, run_pipeline
from test_evalkit_runs import Recorded


def manifest(run):
    return json.loads((run / "manifest.json").read_text())


def test_normal_resume_reuses_and_keeps_original_records(tmp_path):
    cfg = EvalConfig()
    run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)
    before = manifest(tmp_path / "r")
    again = Recorded()
    run_pipeline(cfg, "r", provider=again, runs_dir=tmp_path)
    after = manifest(tmp_path / "r")
    assert again.calls == 0
    assert after["calls"] == before["calls"]  # judged_at, tokens, cost: all original
    assert after["created_at"] == before["created_at"] and after["fingerprint"] == before["fingerprint"]
    assert after["resumes"][-1]["judged"] == [] and len(after["resumes"][-1]["reused"]) == 11


def test_resume_after_failure_judges_only_the_failed_call(tmp_path):
    class FailOnce(Recorded):
        def complete_json(self, system, user, *, key=None):
            if self.by_sha[key] == "03-reschedule_existing":
                raise ConnectionError("rate limited")
            return super().complete_json(system, user, key=key)

    cfg = EvalConfig(max_retries=0)
    run_pipeline(cfg, "r", provider=FailOnce(), runs_dir=tmp_path)
    first = manifest(tmp_path / "r")
    assert first["calls"]["03-reschedule_existing"]["status"] == "error"
    again = Recorded()
    run_pipeline(cfg, "r", provider=again, runs_dir=tmp_path)
    after = manifest(tmp_path / "r")
    assert again.calls == 1 and after["totals"]["ok"] == 11
    assert after["resumes"][-1]["judged"] == ["03-reschedule_existing"]
    untouched = {k: v for k, v in first["calls"].items() if k != "03-reschedule_existing"}
    assert {k: after["calls"][k] for k in untouched} == untouched


@pytest.mark.parametrize("change", [
    {"model": "gpt-4.1-mini"},
    {"rubric": "v1"},
    {"redact": False},
    {"provider": "bedrock"},
    {"dataset": "synthetic"},
])
def test_changed_settings_refuse_reuse(tmp_path, change):
    cfg = EvalConfig()
    run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)
    before = (tmp_path / "r" / "manifest.json").read_text()
    other = Recorded(model=change.get("model", "gpt-4.1"))
    with pytest.raises(ResumeConflict, match="settings differ.*new run name"):
        run_pipeline(replace(cfg, **change), "r", provider=other, runs_dir=tmp_path, calls=iter_calls())
    # force can't override a settings change either: outputs would mix two configurations
    with pytest.raises(ResumeConflict):
        run_pipeline(replace(cfg, **change), "r", provider=other, runs_dir=tmp_path, calls=iter_calls(), force=True)
    assert other.calls == 0 and (tmp_path / "r" / "manifest.json").read_text() == before


def test_changed_rubric_text_refuses_reuse(tmp_path, monkeypatch):
    from evalkit import rubrics

    cfg = EvalConfig()
    run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)
    monkeypatch.setitem(rubrics.RUBRICS, "v2", rubrics.RUBRICS["v2"] + "\nBe stricter.")
    with pytest.raises(ResumeConflict, match="rubric_sha"):
        run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)


def test_changed_redaction_names_refuse_reuse(tmp_path, monkeypatch):
    cfg = EvalConfig()
    run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)
    monkeypatch.setenv("PATIENT_FIRST_NAME", "Someone")
    monkeypatch.setenv("PATIENT_LAST_NAME", "Else")
    with pytest.raises(ResumeConflict, match="redaction_names_sha"):
        run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)


def _calls_with(edit):
    calls = iter_calls()
    edit(next(c for c in calls if c.name == "10-schedule_weekend"))
    return calls


def test_changed_transcript_refuses_reuse_unless_forced(tmp_path):
    cfg = EvalConfig()
    run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)
    before = (tmp_path / "r" / "manifest.json").read_text()

    def edit(c):
        c.transcript += "\nAGENT: one more line"

    with pytest.raises(ResumeConflict, match="10-schedule_weekend \\(transcript changed\\).*--force"):
        run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path, calls=_calls_with(edit))
    assert (tmp_path / "r" / "manifest.json").read_text() == before

    class AnyKey(Recorded):  # the edited transcript has a new hash; answer with the original output
        def complete_json(self, system, user, *, key=None):
            return super().complete_json(system, user, key=key if key in self.by_sha else
                                         next(s for s, c in self.by_sha.items() if c == "10-schedule_weekend"))

    p = AnyKey()
    calls = _calls_with(edit)
    run_pipeline(cfg, "r", provider=p, runs_dir=tmp_path, calls=calls, force=True)
    after = manifest(tmp_path / "r")
    assert p.calls == 11  # --force re-judges everything under the same settings
    new_sha = next(c for c in calls if c.name == "10-schedule_weekend").input_sha
    assert after["calls"]["10-schedule_weekend"]["input_sha"] == new_sha


def test_changed_scenario_refuses_reuse(tmp_path):
    cfg = EvalConfig()
    run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path)

    def edit(c):
        c.scenario = {**c.scenario, "expected": c.scenario.get("expected", "") + " (edited)"}

    with pytest.raises(ResumeConflict, match="scenario changed"):
        run_pipeline(cfg, "r", provider=Recorded(), runs_dir=tmp_path, calls=_calls_with(edit))


def test_run_without_fingerprint_is_never_extended(tmp_path):
    # The backfilled baseline predates provenance tracking: resuming into a copy must refuse
    # rather than stamp it with today's settings.
    shutil.copytree(RUNS_DIR / "baseline-v1-gpt-4.1", tmp_path / "baseline")
    before = (tmp_path / "baseline" / "manifest.json").read_text()
    with pytest.raises(ResumeConflict, match="no provenance fingerprint"):
        run_pipeline(EvalConfig(rubric="v1", redact=False), "baseline", provider=Recorded(), runs_dir=tmp_path)
    assert (tmp_path / "baseline" / "manifest.json").read_text() == before


def test_cli_reports_conflict_with_exit_code(tmp_path, monkeypatch, capsys):
    from evalkit import cli, pipeline

    real = pipeline.run_pipeline

    def fake(cfg, name, force=False):
        return real(cfg, name, provider=Recorded(model=cfg.model), runs_dir=tmp_path, force=force)

    monkeypatch.setattr(cli, "run_pipeline", fake)
    monkeypatch.setenv("ANALYSIS_PROVIDER", "openai")
    assert cli.main(["run", "--name", "r", "--model", "gpt-4.1"]) == 0
    assert cli.main(["run", "--name", "r", "--model", "gpt-4.1-mini"]) == 2
    assert "refusing to resume" in capsys.readouterr().err
