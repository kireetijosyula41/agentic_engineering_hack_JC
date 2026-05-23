# PolicyPulse — Autonomous Prior-Auth Intelligence

> Prior authorization is not merely inefficient — it is actively harming patients and clinicians.
> These are not edge cases. This is standard operating procedure across American healthcare.

---

## The Problem

The prior authorization system is broken at scale:

| Stat | Impact |
|---|---|
| **16+ hrs/week** | Time physicians spend on PA appeals alone |
| **82%** | Physicians whose patients abandon treatment due to PA delays |
| **94%** | Doctors who say PA directly contributes to burnout |
| **29%** | Doctors who report PA delays caused serious adverse events — including death |
| **$25.7B** | Claims adjudication cost incurred by providers in 2023 (up 23% YoY) |
| **53M** | Prior-auth determinations in Medicare Advantage alone in 2024 |
| **80.7%** | Of appealed denials that are *reversed* — meaning they never should have been denied |

The root cause is structural: **payer policies change constantly, and clinicians have no automated way to know when a change affects the care they are currently ordering.** The result is documentation submitted too late, with the wrong evidence, against a policy that changed last week.

PolicyPulse is built to catch that gap upstream, before submission.

---

## What PolicyPulse Does

PolicyPulse is a **fully autonomous agent** that:

1. Continuously monitors trusted payer policy sources for changes
2. Classifies every change as `material`, `non_material`, or `ambiguous`
3. Waits silently until a clinician action arrives via CDS Hooks
4. Checks whether the policy change is **relevant** to what the doctor is currently ordering
5. If relevant — and only then — surfaces CRD guidance, assembles a DTR checklist, gates it behind an x402 micropayment, retrieves grounded documentation requirements from Senso, and publishes the result to cited.md
6. Logs every decision — including every suppressed alert — to ClickHouse for full auditability

No alert is lost. No clinician is interrupted unnecessarily.

---

## Autonomous Agent Architecture

```
Trusted Source Registry (fixtures/trusted_sources.json)
        │
        ▼
  Nimble fetches approved payer-policy URL
  (live CLI with fixture fallback — zero demo risk)
        │
        ▼
  ClickHouse stores baseline + current snapshot
  (dual-write: ClickHouse Cloud + local JSONL fallback)
        │
        ▼
  PolicyPulse computes unified diff (SHA-256 content hash)
        │
        ▼
  Materiality Classifier
  ├── material     → add to active watchlist
  ├── ambiguous    → flag for human review
  └── non_material → log only, suppress
        │
        ▼  [background thread polling every 15 seconds]
        │
        ▼
  CDS Hooks ingestion (order-select / order-sign)
  EHR trigger saved to ClickHouse ehr_events
        │
        ▼
  CRD Opportunity Evaluator
  ├── payer match?
  ├── plan match?
  ├── procedure / CPT keyword match?
  └── provider action match?
        │
  ┌─────┴──────────────────────────────┐
  │                                    │
  ▼                                    ▼
crd_guidance_ready               suppress / human_review
  │                              (logged, not surfaced)
  ▼
  DTR Requirements assembled
        │
        ▼
  x402 Payment Gate (HTTP 402 → demo-paid token)
        │
        ▼
  Senso grounded checklist retrieval
  (live CLI query or fixture fallback)
        │
        ▼
  ReadinessCard assembled
        │
        ▼
  Senso publish to cited.md
        │
        ▼
  ClickHouse KPI update
  (readiness_card_generated, payment_verified, etc.)
```

The entire loop runs without human input. The background monitor thread (`PolicyPulseService._monitor_loop`) starts when the UI first loads, scans on a 15-second interval, and writes every event to ClickHouse before the first user interaction.

---

## The Four Routes — Zero Alert Fatigue by Design

Every clinical trigger hits the CRD evaluator and exits one of four paths:

| Route | Condition | Action |
|---|---|---|
| `crd_guidance_ready` | material change + payer/plan/procedure match + ordering action | DTR checklist → payment gate → Senso card |
| `suppress` | No active material change aligns with this doctor action | Logged to ClickHouse; clinician sees nothing |
| `human_review` | Ambiguous policy change language | Flagged for clinical review before surfacing |
| `log_only` | Non-material change detected at watch time | Stored in ClickHouse; never reaches CRD |

**Every suppressed alert is logged, not discarded.** The audit trail in ClickHouse answers the question "why didn't you alert me?" as reliably as it answers "why did you alert me?"

---

## Technology Stack

| Sponsor / Tool | Role |
|---|---|
| **Nimble** | Fetches public payer-policy pages; normalizes markdown into structured policy records; live CLI with fixture fallback |
| **ClickHouse** | Stores every event — policy snapshots, diffs, materiality decisions, EHR triggers, CRD decisions, payment events, KPIs — as the system's durable event memory |
| **Senso** | Retrieves source-grounded documentation requirements for the readiness checklist; publishes the final readiness card to cited.md |
| **x402 / CDP** | Gates the premium readiness card behind an HTTP 402 micropayment flow — agents and clients pay programmatically |
| **CDS Hooks** | Receives `order-select` and `order-sign` triggers from the EHR workflow layer |
| **CRD** | Evaluates whether a live clinical trigger aligns with a watched material policy change |
| **DTR** | Assembles the documentation evidence list required before prior-auth submission |
| **FastAPI** | REST API layer for all agent services |
| **Streamlit** | Live event-driven UI — auto-bootstraps the autonomous monitor on load |

**Production scale path:** Myndshft for normalized multi-payer rule requirements, Availity for authorization workflow context, Accountable HQ for HIPAA compliance and audit trail controls.

---

## How the Demo Runs End-to-End

The hackathon demo targets UHC public policy pages and simulates one realistic policy update to run the full autonomous loop:

**Step 1 — Autonomous monitor starts on UI load**

The Streamlit UI calls `/api/monitor/start` on boot. The backend spins up a daemon thread that:
- Checks whether a material change already exists for the selected source
- If not, seeds the baseline snapshot and runs `_ensure_demo_material_change` to produce a realistic diff immediately
- Continues polling every 15 seconds for the session lifetime

**Step 2 — Policy diff surfaces in Panel 1**

Nimble fetches the "new" policy fixture. PolicyPulse computes a line-level unified diff, classifies it as `material` (keywords: `prior authorization`, `documentation`, `medical necessity`), and writes the `ActivePolicyChange` to ClickHouse.

**Step 3 — Doctor EHR action arrives**

A simulated `order-sign` CDS Hook arrives for a UHC MRI lumbar spine order. The CRD evaluator checks: payer ✓, plan ✓, procedure keywords ✓, ordering action ✓ → `crd_guidance_ready`.

Use the **irrelevant doctor fixture** to demonstrate suppression — the agent stays completely quiet when the workflow does not match the watched change.

**Step 4 — Payment gate**

The readiness card endpoint returns HTTP `402 Payment Required`. The UI re-submits with `payment_token=demo-paid`, which the x402 adapter verifies and logs as `payment_verified` in ClickHouse.

**Step 5 — Senso retrieval and cited.md publish**

Senso returns the grounded checklist for the payer + procedure combination. The ReadinessCard is assembled and published to cited.md via the Senso publish API.

**Step 6 — ClickHouse audit trail**

Every step — `nimble_fetch_started`, `policy_diff_generated`, `materiality_classified`, `cds_order_sign_received`, `crd_guidance_ready`, `dtr_requested`, `payment_verified`, `senso_checklist_retrieved`, `readiness_card_generated` — is written to ClickHouse in order. The full event timeline is queryable.

---

## Key KPIs PolicyPulse Tracks

| KPI | Definition |
|---|---|
| **First-Pass PA Readiness Rate** | % of required evidence items present at submission |
| **Avoided Denial & Rework Cost** | Admin savings from catching missing evidence before submission |
| **Avoided Adjudication Burden** | Reduction in unnecessary denial workload upstream |
| **Revenue-Cycle Labor Cost Avoided** | Hours eliminated from manual payer-rule checking |
| **Denied-PA Dollar Exposure Reduced** | Value of denied PA volume where readiness improvement reduces rework |

With 80.7% of appealed denials reversed, the dollars PolicyPulse catches upstream are not edge cases — they are the majority of denials.

---

## Project Structure

```
app/
├── core/
│   ├── classifier.py     # materiality classification
│   ├── cds.py            # CDS Hooks ingestion
│   ├── crd_dtr.py        # CRD opportunity evaluation + DTR assembly
│   ├── diffing.py        # SHA-256 snapshot diff
│   └── readiness.py      # ReadinessCard builder
├── integrations/
│   ├── nimble.py         # Nimble live CLI + fixture fallback
│   ├── senso.py          # Senso search + cited.md publish
│   ├── clickhouse_store.py  # ClickHouse + local JSONL dual-write
│   └── payment.py        # x402 payment gate
├── ui/
│   └── streamlit_app.py  # event-driven live UI
├── services.py           # PolicyPulseService — the autonomous agent core
├── models.py             # Pydantic models for every event type
└── runtime.py            # service singleton

fixtures/
├── trusted_sources.json          # approved payer policy URLs
├── policy_snapshot_old_*.txt     # baseline policy fixtures
├── policy_snapshot_new_*.txt     # updated policy fixtures (with material diff)
├── cds_order_sign_matching.json  # relevant doctor action
├── cds_order_sign_nonmatching.json  # irrelevant doctor action
└── senso_checklist_example.json  # grounded checklist fallback

.runtime_data/
├── agent_events.jsonl        # ClickHouse fallback event log
├── policy_snapshots.jsonl    # snapshot store
├── policy_changes.jsonl      # change records
└── ehr_events.jsonl          # EHR trigger log
```

---

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill environment variables
cp .env.example .env

# Start the API
uvicorn app.api.main:app --reload

# Start the UI (in a second terminal)
streamlit run app/ui/streamlit_app.py
```

The UI expects the API at `http://localhost:8000`. Override with `POLICYPULSE_API_BASE`.

### Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `NIMBLE_API_KEY` | Optional | Falls back to policy fixture files |
| `SENSO_API_KEY` | Optional | Falls back to checklist fixture file |
| `CLICKHOUSE_HOST` | Optional | Falls back to local `.runtime_data/` JSONL files |
| `CLICKHOUSE_USER` | Optional | Default: `default` |
| `CLICKHOUSE_PASSWORD` | Optional | |
| `CLICKHOUSE_PORT` | Optional | Default: `8443` |

Every integration has a fixture fallback. The demo runs end-to-end with zero API keys configured.

---

## Why PolicyPulse Wins

**The system is autonomous by design, not by claim.**

The background monitor thread starts before the first user interaction. Policy changes are detected, classified, and written to ClickHouse before a doctor ever opens the dashboard. When the EHR trigger arrives, the agent already knows what changed and whether it matters. The clinician is interrupted only when the intersection of *what changed* and *what the doctor is doing right now* is non-empty.

That is not a demo gimmick. That is the only architecture that could actually eliminate 16+ hours of weekly PA burden — by moving the work to the agent and the machine, and giving clinicians back their time.

**The suppression path is as important as the alert path.**

83% of nurses are overwhelmed by alarm fatigue. Every prior-auth system that has tried to "help" clinicians has done so by adding more alerts. PolicyPulse is architected around the opposite principle: the agent earns the right to interrupt by demonstrating it knows when *not* to.

**The audit trail is not optional.**

Every suppressed alert, every non-material classification, every payment event is written to ClickHouse. In a healthcare compliance context, "we logged it" is not a feature — it is a prerequisite. The ClickHouse event store makes PolicyPulse auditable by design.
