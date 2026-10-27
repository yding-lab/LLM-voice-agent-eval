"""End-to-end pipeline with a stand-in judge: manifests, resume, cost, comparison."""

import json

from evalkit.compare import compare
from evalkit.config import EvalConfig
from evalkit.data import iter_calls
from evalkit.paths import RUNS_DIR
from evalkit.pipeline import run_pipeline
from evalkit.providers import Completion
from evalkit.runs import load_run


class Recorded:
    """Answers with the recorded baseline output; optionally flips one check to fail."""
    name = "openai"

    def __init__(self, model="gpt-4.1", flip=None):
        self.model, self.flip, self.calls = model, flip, 0
        self.outputs, manifest = load_run(RUNS_DIR / "baseline-v1-gpt-4.1")
        self.by_sha = {m["input_sha"]: c for c, m in manifest["calls"].items()}

    def complete_json(self, system, user, *, key=None):
        self.calls += 1
        call = self.by_sha[key]
        out = json.loads(json.dumps(self.outputs[call]))
        if self.flip and self.flip[0] == call:
            for c in out["checks"]:
                if c["id"] == self.flip[1]:
                    c["result"] = "fail"
        return Completion(out, 10_000, 1_000)


def test_pipeline_writes_outputs_manifest_and_report(tmp_path):
    cfg = EvalConfig(provider="openai", model="gpt-4.1", rubric="v2")
    res = run_pipeline(cfg, "r1", provider=Recorded(), runs_dir=tmp_path)
    run = tmp_path / "r1"
    m = json.loads((run / "manifest.json").read_text())
    assert m["totals"]["ok"] == 11 and m["totals"]["errors"] == 0
    assert m["config"]["rubric"] == "v2" and len(m["rubric_sha"]) == 12 and m["scenario_set_sha"]
    assert m["calls"]["07-barge_in"]["redactions"].get("DOB", 0) >= 1  # PII was pseudonymized
    # 11 calls x (10k in, 1k out) at $2 / $8 per 1M tokens
    assert m["totals"]["cost_usd"] == round(11 * (10_000 * 2 + 1_000 * 8) / 1e6, 4)
    assert (run / "report.md").exists() and res["scores"]["dev"]["precision"]["k"] == 7


def test_pipeline_resumes_without_re_judging(tmp_path):
    cfg = EvalConfig()
    first = Recorded()
    run_pipeline(cfg, "r", provider=first, runs_dir=tmp_path)
    again = Recorded()
    run_pipeline(cfg, "r", provider=again, runs_dir=tmp_path)
    assert first.calls == 11 and again.calls == 0


def test_one_failed_call_does_not_sink_the_run(tmp_path):
    class Broken(Recorded):
        def complete_json(self, system, user, *, key=None):
            if self.by_sha[key] == "03-reschedule_existing":
                raise ConnectionError("boom")
            return super().complete_json(system, user, key=key)

    cfg = EvalConfig(max_retries=0)
    res = run_pipeline(cfg, "r", provider=Broken(), runs_dir=tmp_path, calls=iter_calls())
    t = res["manifest"]["totals"]
    assert (t["ok"], t["errors"]) == (10, 1)
    assert "ConnectionError" in res["manifest"]["calls"]["03-reschedule_existing"]["error"]


def test_compare_reports_setup_metric_and_check_changes(tmp_path):
    run_pipeline(EvalConfig(rubric="v1"), "before", provider=Recorded(), runs_dir=tmp_path)
    run_pipeline(EvalConfig(rubric="v2", model="gpt-4.1-mini"), "after",
                 provider=Recorded(model="gpt-4.1-mini", flip=("10-schedule_weekend", "WKD-01")), runs_dir=tmp_path)
    r = compare(tmp_path / "before", tmp_path / "after")
    assert r["setup_changes"]["rubric"] == {"before": "v1", "after": "v2"}
    assert r["setup_changes"]["model"] == {"before": "gpt-4.1", "after": "gpt-4.1-mini"}
    assert r["checks"]["newly_failing"] == [{"call": "10-schedule_weekend", "check": "WKD-01",
                                             "severity": "Critical", "before": "pass", "after": "fail"}]
    before_p, after_p = r["metrics"]["dev"]["precision"]
    assert after_p > before_p  # WKD-01 is a labelled real defect, so precision rises
    assert r["cost_and_latency"]["cost_usd"][1] < r["cost_and_latency"]["cost_usd"][0]  # mini is cheaper
