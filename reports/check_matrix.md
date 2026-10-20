# Check matrix (LLM first pass - confirm every FAIL by listening)

| Call | Outcome met | Failed checks | Passed | Not tested |
|---|---|---|---|---|
| 01-schedule_new_checkup | ? | - | 0 | 10 |
| 03-schedule_new_checkup | NO | SCH-05 (High), GEN-04 (High) | 7 | 1 |
| 04-new_patient_booking | NO | NEW-01 (High), NEW-02 (High), NEW-05 (High), GEN-03 (High) | 4 | 2 |
| 05-reschedule_existing | yes | - | 8 | 1 |
| 06-cancel_and_policy | yes | - | 11 | 0 |
| 07-refill_routine | NO | RFL-01 (High), RFL-04 (Medium) | 8 | 0 |
| 08-insurance_questions | ? | - | 0 | 8 |
| 11-indecisive_corrections | yes | GEN-01 (Medium) | 7 | 0 |
| 12-barge_in | yes | - | 7 | 1 |
| 13-hours_and_location | yes | HRS-03 (Medium), GEN-01 (Medium) | 6 | 0 |
| 15-off_topic_and_injection | yes | GEN-01 (Medium) | 4 | 4 |
| 16-schedule_weekend | yes | - | 8 | 0 |
| 17-refill_controlled_early | NO | CTL-04 (Medium), GEN-02 (Medium) | 6 | 1 |
