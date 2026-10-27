"""The HTTP API in keyless replay mode, as the Docker image runs it."""

import pytest
from fastapi.testclient import TestClient

from evalkit import api


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ANALYSIS_PROVIDER", "replay")
    for f in (api.settings, api.provider, api.sample_calls):
        f.cache_clear()
    yield TestClient(api.app)
    for f in (api.settings, api.provider, api.sample_calls):
        f.cache_clear()


def test_health_and_inputs(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["provider"] == "replay" and h["sample_calls"] == 11
    assert len(client.get("/scenarios").json()) == 15
    assert len(client.get("/samples").json()) == 11


def test_evaluate_sample_returns_findings_report_and_human_score(client):
    r = client.post("/samples/02-new_patient_booking/evaluate")
    assert r.status_code == 200
    j = r.json()
    assert j["counts"]["fail"] == 4 and j["cost_usd"] == 0.0
    assert "| NEW-02 |" in j["report_markdown"]
    assert {b["id"]: b["status"] for b in j["human_label_score"]["known_bugs"]} == {1: "detected", 2: "detected"}


def test_evaluate_pasted_sample_transcript(client):
    t = client.get("/samples/10-schedule_weekend").json()["transcript"]
    r = client.post("/evaluate", json={"transcript": t})
    assert r.status_code == 200 and r.json()["counts"]["pass"] == 8


def test_replay_mode_rejects_new_transcripts_with_a_clear_message(client):
    r = client.post("/evaluate", json={"transcript": "CLINIC AGENT: hello", "scenario_id": "refill_routine"})
    assert r.status_code == 422 and "replay mode" in r.json()["detail"]


def test_runs_and_compare(client):
    assert client.get("/runs").json()[0]["run"] == "baseline-v1-gpt-4.1"
    d = client.get("/runs/baseline-v1-gpt-4.1").json()
    assert d["scores"]["dev"]["precision"]["k"] == 7
    assert client.get("/runs/nope").status_code == 404
    c = client.get("/compare", params={"before": "baseline-v1-gpt-4.1", "after": "baseline-v1-gpt-4.1"})
    assert c.status_code == 200 and c.json()["checks"]["newly_failing"] == []
