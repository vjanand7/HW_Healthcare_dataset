# Diabetes Readmission Intervention Queue

**Enterprise healthcare MVP** — Testleaf FDE Program, Week 2 Mini Project.

> **Learning prototype.** The scoring rule is a simple, explainable set of learning assumptions. It is
> not clinically validated and must not be used for real patient care decisions.

## 1. Business problem
A hospital has historical diabetes encounter data and is concerned about patients returning within
30 days of discharge. The care-management team cannot manually review every discharged patient. This
application turns the data into an operational workflow: ingest it, apply an explainable
prioritization rule, persist it, expose it through APIs, and measure whether the prioritization lines
up with historical outcomes.

**Primary business decision:** which recently discharged diabetes patients should the
care-management team review first?

## 2. Users and business outcomes
| User | What they need | Business outcome |
| :--- | :--- | :--- |
| Care Management Team | Prioritized review queue | Focus limited capacity on higher-risk cases |
| Hospital Operations | Queue and workload visibility | Understand intervention volume and backlog |
| Clinical Leadership | Explainable rationale | Review whether prioritization is operationally sensible |
| Finance / Transformation | Outcome and value signals | Assess whether the intervention is worth scaling |

## 3. FDE discovery before coding
| What the dataset tells us | What we do NOT know | MVP hypothesis |
| :--- | :--- | :--- |
| Historical encounters | Which readmissions were preventable? | Prior utilization may help prioritize follow-up |
| Historical <30-day readmissions | Cost of a readmission? | Care-management capacity is limited |
| Prior healthcare utilization | Current discharge workflow? | A risk queue may be better than reviewing everyone |
| Operational / treatment attributes | Cost of an intervention? | Rules are acceptable for an explainable MVP |
| Historical outcome | Clinical validity of the rules? | Historical outcomes can evaluate the rule after scoring |

## 4. Dataset fields used
A focused subset of the 50-column source file (`data/diabetic_data.csv`, 101,766 encounters):
`encounter_id`, `patient_nbr`, `age`, `time_in_hospital`, `number_inpatient`, `number_emergency`,
`number_outpatient`, `num_medications`, `num_lab_procedures`, `discharge_disposition_id`,
`admission_type_id`, `readmitted`.

**Data-leakage rule:** `readmitted` is the historical outcome. It is **never** used to calculate the
risk score — the scorer is only given the five scoring fields (`service.SCORING_FIELDS`), and tests
check this. `readmitted` is used only afterwards, to evaluate the prioritization.

**Missing data:** blank numeric values are treated as 0; rows without an `encounter_id` or
`patient_nbr` are skipped (and counted); a repeated `encounter_id` keeps the first row.

## 5. Prioritization rule (explainable, learning assumption)
| Condition | Points |
| :--- | :--- |
| `number_inpatient >= 3` | +3 |
| `number_emergency >= 2` | +2 |
| `number_outpatient >= 3` | +1 |
| `time_in_hospital >= 7` | +1 |
| `num_medications >= 20` | +1 |

**Risk level:** 0–2 = LOW · 3–4 = MEDIUM · 5+ = HIGH (maximum score 8).

## 6. Architecture
```
Healthcare CSV  →  data_loader.py  →  PostgreSQL  ←  repository.py  ←  service.py  ←  main.py (FastAPI)  ←  Swagger / API consumer
```
| File | Role |
| :--- | :--- |
| `main.py` | Request/response handling: routes, input limits, error handlers |
| `models.py` | Pydantic models and the allowed intervention statuses |
| `service.py` | Business rules: risk scoring, "closed cases stay closed" |
| `repository.py` | All SQL — every value is a bound parameter, no string-built queries |
| `database.py` | Database configuration (`DATABASE_URL`, 5-second connect timeout) |
| `data_loader.py` | Repeatable CSV → PostgreSQL load with scoring |
| `schema.sql` | Tables, constraints, index |
| `seed_or_setup.sql` | One-time user/database setup (plus optional demo rows) |

## 7. PostgreSQL design
- **`patient_encounters`** — the 12 dataset fields plus `risk_score` and `risk_level` (`LOW`/`MEDIUM`/`HIGH`).
- **`care_interventions`** — `intervention_id`, `encounter_id` (unique, foreign key), `status`,
  `assigned_to`, `notes`, `created_at`, `updated_at`.
- Workflow: `PENDING_REVIEW → ASSIGNED → CONTACTED → FOLLOW_UP_SCHEDULED → CLOSED`.
- Constraints enforce valid statuses and risk levels and one intervention per encounter; an index serves
  the care queue.

## 8. API
| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `GET` | `/encounters` | List encounters (paginated) |
| `GET` | `/encounters/{encounter_id}` | Get one encounter; `404` if missing |
| `GET` | `/patients/{patient_nbr}/encounters` | Full encounter history for one patient; `404` if unknown |
| `GET` | `/analytics/age-groups` | Volume, average stay, average risk and <30-day readmission rate per age band |
| `GET` | `/analytics/utilization` | <30-day readmission rate by prior inpatient visits (0, 1, 2, 3+) |
| `GET` | `/care-queue` | Open MEDIUM + HIGH risk queue |
| `GET` | `/care-queue/high` | Open HIGH risk queue only |
| `PATCH` | `/interventions/{encounter_id}` | Update workflow status / owner / notes; `404` if the encounter is missing |
| `GET` | `/operations/summary` | Operational metrics |
| `GET` | `/operations/readmission-analysis` | Compare every risk group against historical <30-day readmissions |
| `GET` | `/management/dashboard` | Executive summary: operations + clinical validation |

Example `PATCH /interventions/12345`:
```json
{ "status": "CONTACTED", "assigned_to": "Care Team A", "notes": "Follow-up appointment scheduled" }
```

### Behavior worth knowing
- **Paging:** `limit` (0–1000) and `offset` (≥ 0); defaults 100 for `/encounters`, 50 for the queues. Invalid values → `422`.
  Queue ties are broken by `encounter_id`, so pages never overlap or skip rows.
- **Queue:** open cases only. A case nobody has touched shows `PENDING_REVIEW`; `CLOSED` cases leave the queue.
- **`PATCH`:** `status` must be one of the five values above (exact capitals) or `422`. `assigned_to` (max 100
  characters) and `notes` change only if included; send `null` to clear one. A `CLOSED` case cannot be moved to
  another status (`409`). Intervention status is stored in PostgreSQL, so it survives API restarts.
- **Summary:** `pending_interventions` = MEDIUM/HIGH cases nobody has started + any case marked `PENDING_REVIEW`;
  `completed_interventions` = `CLOSED` cases. All metrics are calculated from stored data.
- **Readmission analysis:** the original `high_risk_*` fields plus `risk_groups` (HIGH / MEDIUM / LOW).
- **Errors:** database unreachable or `DATABASE_URL` missing → `503`.

## 9. Run instructions
```bash
pip install -r requirements.txt
```
1. Create the database user and database (edit the password first): `psql -U postgres -f seed_or_setup.sql`
2. Copy `.env.example` to `.env` and set `DATABASE_URL` (same password).
3. Create the tables: `psql -U fde_user -d fde_db -f schema.sql`
4. Put `diabetic_data.csv` in `data/`, then load and score it (about 5 seconds): `python data_loader.py`
   Re-running is safe: existing rows are refreshed and care interventions are kept.
5. Start the API: `uvicorn main:app --reload` → Swagger UI at http://127.0.0.1:8000/docs

If the tables came from an older `schema.sql`, add the new safeguards:
```sql
ALTER TABLE care_interventions ADD CONSTRAINT care_interventions_encounter_id_key UNIQUE (encounter_id);
ALTER TABLE care_interventions ADD CONSTRAINT care_interventions_status_check
    CHECK (status IN ('PENDING_REVIEW','ASSIGNED','CONTACTED','FOLLOW_UP_SCHEDULED','CLOSED'));
ALTER TABLE patient_encounters ADD CONSTRAINT patient_encounters_risk_level_check
    CHECK (risk_level IN ('LOW','MEDIUM','HIGH'));
CREATE INDEX idx_encounters_queue ON patient_encounters (risk_level, risk_score DESC, encounter_id);
```
(The unique constraint fails if an encounter already has more than one intervention — remove the extras first.)

## 10. Results on the dataset
Measured by loading the full dataset and calling `/operations/summary` and `/operations/readmission-analysis`:

| Risk level | Encounters | Readmitted <30 days | Rate |
| :--- | ---: | ---: | ---: |
| HIGH | 2,358 | 674 | **28.58%** |
| MEDIUM | 6,180 | 1,335 | **21.60%** |
| LOW | 93,228 | 9,348 | **10.03%** |
| All | 101,766 | 11,357 | 11.16% |

The rate rises with the risk level, so the rule does separate higher-risk from lower-risk encounters: HIGH is
about 2.9× LOW and 2.6× the overall rate. The care queue (MEDIUM + HIGH) holds 8,538 encounters (8.4% of all)
with a 23.5% readmission rate, and contains 2,009 of the 11,357 readmissions (17.7%).

## 11. FDE reflection
1. **What business problem did you solve?** The care team cannot review every discharge. The app converts
   historical encounters into a ranked, trackable review queue, with workload and outcome metrics.
2. **Who is the primary user?** The care-management team. Operations, clinical leadership and finance are
   secondary users of the metrics and dashboard.
3. **What decision does your application improve?** Which recently discharged diabetes patients to review first.
   Instead of an arbitrary order, reviewers start with the highest-scoring open cases and can see why a case ranks
   where it does (five transparent rules).
4. **What did the dataset prove?** That the simple rule has some value: readmission within 30 days rises from
   10.03% (LOW) to 21.60% (MEDIUM) to 28.58% (HIGH). It also showed the limits: most readmissions (9,348 of 11,357)
   are in the LOW group, so the queue catches under a fifth of them. This is a historical association, not proof
   that reviewing these patients prevents readmissions.
5. **What assumptions did you make?** The weights and thresholds are learning assumptions, not clinical
   recommendations; each row is one encounter (the same patient can appear more than once — 101,766 encounters
   for 71,518 patients); missing numeric values count as 0; the historical `readmitted = '<30'` label is a fair
   measure of a "bad outcome"; and a case nobody has touched is waiting for review.
6. **What must the hospital validate before this could become a real product?** Clinical validity and fairness of
   the rules; which readmissions are preventable; whether the data is current and representative of today's
   patients; that patients who should not be followed up (for example, those who died or went to hospice) are
   excluded; the cost of a readmission versus the cost and capacity of an intervention; the real discharge
   workflow; and privacy, security and audit requirements for patient data.
7. **Would you use rules, traditional ML, an LLM, or another approach next — and why?** Keep the rules as the
   explainable baseline and try traditional ML (such as logistic regression or gradient-boosted trees) next,
   evaluated on a held-out period and compared against the rule — because the LOW group still holds most
   readmissions and a model can use more of the available fields. Any model must stay explainable and be checked
   for bias before use. An LLM could help summarize case notes for reviewers, but not to rank patients.

## 12. Testing
```bash
pip install httpx
python -m unittest test_api_qa -v
```
**118 tests, all passing (about 1 minute).** Needs a reachable PostgreSQL and `DATABASE_URL`. Every test runs in a
temporary schema built from `schema.sql` and dropped afterwards, so real tables are never touched. Set
`SKIP_REAL_DATA=1` to skip the full-dataset tests.

| Suite | Covers |
| :--- | :--- |
| Risk scoring | Every rule boundary, level boundaries, maximum score, null/missing/negative values, no use of `readmitted` |
| Schema | Defaults, keys, NOT NULL, one intervention per encounter, valid statuses and levels |
| Encounter endpoints | Paging, invalid and out-of-range values, 404s, SQL-injection strings, Unicode and long IDs |
| Care queue | Only MEDIUM/HIGH, score order, stable paging with ties, default `PENDING_REVIEW`, closed cases leave the queue |
| Interventions | Create/update, partial updates, explicit null, invalid status, closed cases stay closed, length limits, special characters |
| Operations | Counts, pending definition, empty database, readmission rates, all risk groups, dashboard |
| Robustness | Database down → 503 and fails fast, missing `DATABASE_URL`, unknown routes, OpenAPI |
| Data loader | Scoring, reload refreshes rows, duplicates, missing values, blank IDs skipped, missing columns, interventions kept |
| Full dataset (101,766 rows) | Counts, scores and readmission rates against an independent pandas calculation; queue order and paging; response time |

QA found 24 defects in an earlier version (a partial `PATCH` erasing data, unvalidated statuses, queue paging that
skipped rows, an understated pending count, 500 errors on bad input, and others); all were fixed and are covered by
the tests above.

## 13. Submission deliverables
| Deliverable | Where |
| :--- | :--- |
| README with problem, users, assumptions, architecture, API list, run instructions, reflection | This file |
| `schema.sql`, `seed_or_setup.sql`, `data_loader.py`, FastAPI source | This folder |
| Swagger screenshot showing key endpoints | `Project_Screenshots/healthcare-readmission-app.png` |
| PostgreSQL screenshots showing loaded records | `Project_Screenshots/db_screenshot_1.png` – `db_screenshot_3.png` |
| 3–5 minute demo video | To record |

*Learning use only — not for clinical decision-making.*
