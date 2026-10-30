"""Second-reviewer adjudication, reproducing runs, and the synthetic offline demo."""

import json
import shutil

import pytest
import yaml

from evalkit import cli, pipeline
from evalkit.paths import DATASETS, Dataset
from evalkit.runs import load_run
from evalkit.scoring import disagreements, load_labels, score_by_split, truth_labels, truth_source

from test_evalkit_runs import Recorded


def _labels_dir(tmp_path, flip=None, adjudicate=None):
    truth = load_labels()
    (tmp_path / "reviewer_a.yaml").write_text(yaml.safe_dump(truth))
    b = {"judge_fails": {c: {k: {"label": v["label"]} for k, v in checks.items()}
                         for c, checks in truth["judge_fails"].items()}}
    if flip:
        call, check, label = flip
        b["judge_fails"][call][check]["label"] = label
    (tmp_path / "reviewer_b.yaml").write_text(yaml.safe_dump(b))
    if adjudicate:
        adj = json.loads(json.dumps(truth))
        call, check, label = adjudicate
        adj["judge_fails"][call][check]["label"] = label
        (tmp_path / "adjudicated.yaml").write_text(yaml.safe_dump(adj))
    return tmp_path


def test_disagreements_list_what_to_adjudicate(tmp_path):
    d = _labels_dir(tmp_path, flip=("08-hours_and_location", "HRS-03", "confirmed"))
    assert disagreements(d) == [{"call": "08-hours_and_location", "check": "HRS-03",
                                 "reviewer_a": "not_adopted", "reviewer_b": "confirmed"}]
    assert truth_source(d) == "reviewer_a"


def test_adjudicated_labels_become_the_truth(tmp_path):
    d = _labels_dir(tmp_path, flip=("08-hours_and_location", "HRS-03", "confirmed"),
                    adjudicate=("08-hours_and_location", "HRS-03", "confirmed"))
    assert truth_source(d) == "adjudicated"
    outputs, _ = load_run(DATASETS["recorded"].runs_dir / "baseline-v1-gpt-4.1")
    dev = score_by_split(outputs, truth_labels(d))["dev"]
    assert (dev.precision.k, dev.precision.n) == (8, 14)  # one more fail now counts as a real defect


def test_synthetic_demo_has_its_own_split_and_no_empty_holdout():
    ds = DATASETS["synthetic"]
    outputs, manifest = load_run(ds.runs_dir / "illustrative-fixture")
    assert "NOT the output of any model" in manifest["fixture"]
    scores = score_by_split(outputs, truth_labels(ds.labels_dir), yaml.safe_load(ds.splits_file.read_text())["splits"])
    assert list(scores) == ["synthetic"]
    s = scores["synthetic"]
    assert (s.precision.k, s.precision.n) == (4, 6) and (s.recall().k, s.recall().n) == (4, 5)


def test_demo_commands_run_offline(capsys):
    assert cli.main(["demo"]) == 0
    assert "SYNTHETIC" in capsys.readouterr().out
    assert cli.main(["demo", "--dataset", "recorded"]) == 0
    assert "50% (7/14" in capsys.readouterr().out


def test_reproduce_reruns_with_recorded_settings_and_compares(tmp_path, monkeypatch, capsys):
    rec = DATASETS["recorded"]
    runs = tmp_path / "runs"
    shutil.copytree(rec.runs_dir / "baseline-v1-gpt-4.1", runs / "baseline-v1-gpt-4.1")
    ds = Dataset("recorded", rec.calls_dir, rec.labels_dir, rec.splits_file, runs, rec.replay_run)
    monkeypatch.setattr(cli, "get_dataset", lambda name=None: ds)
    monkeypatch.setattr(pipeline, "get_dataset", lambda name=None: ds)
    used = {}

    def fake_provider(cfg):
        used["cfg"] = cfg
        return Recorded(model=cfg.model)

    monkeypatch.setattr(pipeline, "make_provider", fake_provider)
    assert cli.main(["reproduce", "baseline-v1-gpt-4.1", "--name", "repro"]) == 0
    assert (used["cfg"].provider, used["cfg"].model, used["cfg"].rubric, used["cfg"].redact) == ("openai", "gpt-4.1", "v1", False)
    m = json.loads((runs / "repro" / "manifest.json").read_text())
    assert m["config"]["rubric"] == "v1" and m["totals"]["ok"] == 11
    out = capsys.readouterr().out
    assert "baseline-v1-gpt-4.1 → repro" in out and "Newly failing (0)" in out


def test_fixture_runs_are_not_reproducible(capsys):
    assert cli.main(["reproduce", "illustrative-fixture", "--dataset", "synthetic"]) == 1
    assert "can't be re-run" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", [["agreement"], ["runs"], ["score"]])
def test_reporting_commands_succeed(cmd):
    assert cli.main(cmd) == 0
