# Synthetic sample data (offline demo)

Three short call transcripts written by hand for this repository, so the evaluation flow can be explored with no phone line, API key or third-party data.

| | |
|---|---|
| **Provenance** | Written by the repository author for demonstration. Not recordings and not derived from any real call |
| **Clinic and people** | Fictional: "Maple Grove Orthopedics", caller "Jordan Lee", "Dr. Patel", "Maria". Phone numbers use the reserved 555-01xx range |
| **License** | Same as the repository (MIT) |
| **Seeded defects** | Five known agent defects, listed with their checks in `labels/reviewer_a.yaml`. Call `s03` is a correct call, there to catch false alarms |

`runs/illustrative-fixture/` holds **hand-written** judge output for these calls. It is **not** the output of any model. It is a fixture that lets the offline demo run the real pipeline end to end: pseudonymization, tracking, scoring against seeded ground truth, comparison. It deliberately includes one judge mistake of each kind (blaming the caller's overlap on the agent, a lenient pass, a false alarm on a correct call) so every metric has something to show. **Its numbers say nothing about how good any judge is.** For real judge measurements, see `JUDGE_EVAL.md`.

```bash
python -m evalkit demo                     # synthetic data + fixture (default)
python -m evalkit run --dataset synthetic  # judge the synthetic calls with a real model (needs a key)
```
