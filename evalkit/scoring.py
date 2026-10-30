"""Score a judge run against human labels.

Ground truth lives in evals/labels/. Each reviewer labels independently in reviewer_<x>.yaml.
Truth is `adjudicated.yaml` when it exists (disagreements resolved after both reviewers
labelled), otherwise reviewer_a. Further reviewers are compared for agreement (Cohen's kappa),
and `disagreements()` lists exactly what an adjudicator has to decide.

Everything is reported per split (evals/splits.yaml). `dev` holds the calls the current rubric
was designed on, so its numbers are in-sample. Every other call is `holdout`.

Small samples are the norm here, so every rate comes with a 95% Wilson interval.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .paths import LABELS_DIR, SPLITS_FILE

TRUTH = "reviewer_a"
MATCH_WINDOW_S = 5  # an other_finding counts if it cites a time within this many seconds
FAIL_LABELS = ("confirmed", "caused_by_caller", "not_adopted")
SEVERITIES = ("Critical", "High", "Medium", "Low")


# ---------------------------------------------------------------- statistics
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for k successes in n trials. Well behaved for small n and k = 0 or n."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Agreement between two raters beyond chance. 1 = perfect, 0 = chance level."""
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


@dataclass
class Rate:
    k: int
    n: int

    @property
    def value(self) -> float | None:
        return self.k / self.n if self.n else None

    @property
    def ci(self) -> tuple[float, float] | None:
        return wilson(self.k, self.n)

    def fmt(self) -> str:
        if not self.n:
            return "n/a (no data)"
        lo, hi = self.ci
        return f"{self.value:.0%} ({self.k}/{self.n}; 95% CI {lo:.0%}–{hi:.0%})"

    def to_dict(self) -> dict:
        return {"k": self.k, "n": self.n, "value": self.value, "ci95": self.ci}


# ---------------------------------------------------------------- labels and splits
ADJUDICATED = "adjudicated"


def load_labels(reviewer: str = TRUTH, labels_dir: Path = LABELS_DIR) -> dict:
    return yaml.safe_load((labels_dir / f"{reviewer}.yaml").read_text()) or {}


def truth_source(labels_dir: Path = LABELS_DIR) -> str:
    return ADJUDICATED if (labels_dir / f"{ADJUDICATED}.yaml").exists() else TRUTH


def truth_labels(labels_dir: Path = LABELS_DIR) -> dict:
    """Adjudicated labels if they exist, else reviewer_a."""
    return load_labels(truth_source(labels_dir), labels_dir)


def reviewers(labels_dir: Path = LABELS_DIR) -> list[str]:
    return sorted(p.stem for p in labels_dir.glob("reviewer_*.yaml"))


def load_splits(path: Path = SPLITS_FILE) -> dict[str, list[str]]:
    return (yaml.safe_load(path.read_text()) or {}).get("splits", {}) if path.exists() else {}


def split_of(call: str, splits: dict[str, list[str]]) -> str:
    return next((name for name, calls in splits.items() if call in (calls or [])), "holdout")


# ---------------------------------------------------------------- matching
def _seconds(ts: str) -> list[int]:
    return [int(m) * 60 + int(s) for m, s in re.findall(r"(\d{1,2}):(\d{2})", ts or "")]


def cites(finding_ts: str, wanted: list[str]) -> bool:
    got, want = _seconds(finding_ts), [t for w in wanted for t in _seconds(w)]
    if len(got) == 2 and "-" in (finding_ts or ""):  # a range like 01:07-01:45
        return any(got[0] - MATCH_WINDOW_S <= w <= got[1] + MATCH_WINDOW_S for w in want)
    return any(abs(g - w) <= MATCH_WINDOW_S for g in got for w in want)


# ---------------------------------------------------------------- scoring
@dataclass
class Score:
    split: str
    calls: list[str]
    fail_labels: Counter = field(default_factory=Counter)
    fail_labels_by_severity: dict = field(default_factory=dict)  # severity -> Counter
    unlabeled_fails: list[str] = field(default_factory=list)
    bugs: list[dict] = field(default_factory=list)
    lenient: list[str] = field(default_factory=list)

    @property
    def precision(self) -> Rate:
        return Rate(self.fail_labels["confirmed"], sum(self.fail_labels[k] for k in FAIL_LABELS))

    def recall(self, severities: tuple[str, ...] | None = None) -> Rate:
        pool = [b for b in self.bugs if severities is None or b["severity"] in severities]
        return Rate(sum(b["status"] == "detected" for b in pool), len(pool))

    @property
    def miss_types(self) -> Counter:
        return Counter(b["miss_type"] for b in self.bugs if b["status"] != "detected")

    def to_dict(self) -> dict:
        return {
            "split": self.split, "calls": self.calls,
            "precision": self.precision.to_dict(),
            "recall": self.recall().to_dict(),
            "recall_by_severity": {s: self.recall((s,)).to_dict() for s in SEVERITIES},
            "recall_critical_high": self.recall(("Critical", "High")).to_dict(),
            "fail_labels": dict(self.fail_labels),
            "fail_labels_by_severity": {k: dict(v) for k, v in self.fail_labels_by_severity.items()},
            "miss_types": dict(self.miss_types),
            "unlabeled_fails": self.unlabeled_fails,
            "lenient": self.lenient,
            "bugs": self.bugs,
        }


def score(outputs: dict[str, dict], labels: dict, split: str, calls: list[str]) -> Score:
    s = Score(split=split, calls=sorted(c for c in calls if c in outputs))
    judged = {c: outputs[c] for c in s.calls}
    for call, out in judged.items():
        for c in out.get("checks", []):
            if c.get("result") != "fail":
                continue
            label = labels.get("judge_fails", {}).get(call, {}).get(c["id"])
            if label:
                s.fail_labels[label["label"]] += 1
                s.fail_labels_by_severity.setdefault(c.get("severity", "?"), Counter())[label["label"]] += 1
            else:
                s.unlabeled_fails.append(f"{call} {c['id']}")

    for bug in labels.get("known_bugs", []):
        evidence = [d for d in bug["detected_by"] if d["call"] in judged]
        if not evidence:
            continue  # this bug's calls are not in this split
        status, how, lenient_here = "missed", [], False
        for d in evidence:
            out = judged[d["call"]]
            hit = False
            if "check" in d:
                c = next((c for c in out.get("checks", []) if c["id"] == d["check"]), None)
                if c and c.get("result") == "fail":
                    hit = True
                    how.append(f"{d['call'].split('-')[0]} {d['check']}")
                elif c and c.get("result") == "pass":
                    lenient_here = True
                    s.lenient.append(f"bug {bug['id']}: {d['call']} {d['check']} graded pass "
                                     f"(evidence: \"{(c.get('evidence') or '')[:90]}\")")
            else:
                f = next((f for f in out.get("other_findings", []) if cites(f.get("timestamp", ""), d["other_at"])), None)
                if f:
                    hit = True
                    how.append(f"{d['call'].split('-')[0]} other @ {f.get('timestamp')}")
            if hit:
                status = "detected" if not d.get("partial") else ("partial" if status == "missed" else status)
        miss_type = None
        if status != "detected":
            miss_type = "lenient_pass" if lenient_here else ("partial" if status == "partial" else "not_flagged")
        s.bugs.append({"id": bug["id"], "title": bug["title"], "severity": bug["severity"],
                       "status": status, "how": how, "miss_type": miss_type})
    return s


def score_by_split(outputs: dict[str, dict], labels: dict | None = None,
                   splits: dict[str, list[str]] | None = None) -> dict[str, Score]:
    labels = labels if labels is not None else truth_labels()
    splits = splits if splits is not None else load_splits()
    groups: dict[str, list[str]] = {}
    for call in outputs:
        groups.setdefault(split_of(call, splits), []).append(call)
    # A dataset with a dev split always reports holdout, even when empty, so its absence is visible.
    for name in list(splits) + (["holdout"] if "dev" in splits else []):
        groups.setdefault(name, [])
    return {name: score(outputs, labels, name, calls) for name, calls in groups.items()}


# ---------------------------------------------------------------- inter-rater agreement
def _pairs(a: dict, b: dict) -> list[tuple[str, str, str, str]]:
    """(call, check, label_a, label_b) for items both reviewers labelled."""
    return [(call, cid, a[call][cid]["label"], lab["label"])
            for call, checks in (b or {}).items() for cid, lab in (checks or {}).items()
            if lab and lab.get("label") and (a.get(call) or {}).get(cid, {}).get("label")]


def disagreements(labels_dir: Path = LABELS_DIR) -> list[dict]:
    """Items where reviewer_a and another reviewer disagree: the adjudicator's to-do list."""
    a = load_labels(TRUTH, labels_dir).get("judge_fails", {})
    out = []
    for r in reviewers(labels_dir):
        if r == TRUTH:
            continue
        for call, cid, la, lb in _pairs(a, load_labels(r, labels_dir).get("judge_fails", {})):
            if la != lb:
                out.append({"call": call, "check": cid, TRUTH: la, r: lb})
    return out


def agreement(labels_dir: Path = LABELS_DIR) -> list[dict]:
    """Cohen's kappa between reviewer_a and every other independent reviewer, on items both labelled."""
    truth = load_labels(TRUTH, labels_dir).get("judge_fails", {})
    out = []
    for r in reviewers(labels_dir):
        if r == TRUTH:
            continue
        other = load_labels(r, labels_dir).get("judge_fails", {}) or {}
        pairs = [(la, lb) for _, _, la, lb in _pairs(truth, other)]
        binary = [(a == "confirmed", b == "confirmed") for a, b in pairs]
        out.append({"reviewer": r, "items": len(pairs),
                    "kappa_3way": cohen_kappa(pairs), "kappa_real_defect": cohen_kappa(binary),
                    "raw_agreement": (sum(a == b for a, b in pairs) / len(pairs)) if pairs else None})
    return out


def as_dict(scores: dict[str, Score]) -> dict:
    return {name: s.to_dict() for name, s in scores.items()}


__all__ = ["Rate", "Score", "agreement", "as_dict", "cohen_kappa", "disagreements", "load_labels",
           "load_splits", "score", "score_by_split", "truth_labels", "truth_source", "wilson", "asdict"]
