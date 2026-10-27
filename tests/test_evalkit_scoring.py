"""Scoring against human labels: published numbers, intervals, splits, agreement."""

import pytest
import yaml

from evalkit.paths import RUNS_DIR
from evalkit.runs import load_run
from evalkit.scoring import agreement, cohen_kappa, load_labels, score_by_split, wilson


def test_baseline_reproduces_the_published_numbers():
    outputs, _ = load_run(RUNS_DIR / "baseline-v1-gpt-4.1")
    dev = score_by_split(outputs)["dev"]
    assert (dev.precision.k, dev.precision.n) == (7, 14)
    assert (dev.recall().k, dev.recall().n) == (5, 9)
    assert dev.fail_labels["caused_by_caller"] == 5
    assert len(dev.lenient) == 3
    assert dict(dev.miss_types) == {"not_flagged": 2, "lenient_pass": 2}
    lo, hi = dev.precision.ci
    assert round(lo, 2) == 0.27 and round(hi, 2) == 0.73


def test_wilson_interval_known_values_and_edges():
    assert wilson(0, 0) is None
    lo, hi = wilson(5, 9)
    assert round(lo, 3) == 0.267 and round(hi, 3) == 0.811
    assert wilson(0, 5)[0] == 0.0 and wilson(5, 5)[1] == 1.0


def test_cohen_kappa():
    assert cohen_kappa([("a", "a"), ("b", "b"), ("a", "b"), ("b", "b")]) == pytest.approx(0.5)
    assert cohen_kappa([("a", "a"), ("b", "b")]) == 1.0
    assert cohen_kappa([]) is None


def test_calls_outside_dev_are_scored_as_holdout():
    outputs, _ = load_run(RUNS_DIR / "baseline-v1-gpt-4.1")
    outputs["99-new_call"] = {"checks": [{"id": "SCH-01", "result": "fail", "severity": "Critical"}]}
    scores = score_by_split(outputs)
    assert scores["holdout"].calls == ["99-new_call"]
    assert scores["holdout"].unlabeled_fails == ["99-new_call SCH-01"]  # must be labelled before it counts
    assert scores["dev"].precision.n == 14  # dev numbers unaffected


def test_agreement_with_a_second_reviewer(tmp_path):
    truth = load_labels()
    (tmp_path / "reviewer_a.yaml").write_text(yaml.safe_dump(truth))
    other = {"judge_fails": {call: {cid: {"label": lab["label"]} for cid, lab in checks.items()}
                             for call, checks in truth["judge_fails"].items()}}
    other["judge_fails"]["08-hours_and_location"]["HRS-03"]["label"] = "confirmed"  # one disagreement
    (tmp_path / "reviewer_b.yaml").write_text(yaml.safe_dump(other))
    [row] = agreement(tmp_path)
    assert row["reviewer"] == "reviewer_b" and row["items"] == sum(len(v) for v in truth["judge_fails"].values())
    assert 0.8 < row["kappa_3way"] < 1.0
