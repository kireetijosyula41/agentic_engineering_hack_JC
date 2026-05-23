# TotalPlan.md — PolicyPulse 3-Hour Hackathon MVP With CDS Hooks, CRD, DTR, and Payment Scaffold

## Summary
Build the smallest end-to-end MVP that still captures the full product idea: an autonomous agent that watches trusted payer policy sources for changes, determines whether a change is material, waits for clinical workflow triggers, checks whether the changed policy is relevant to the doctor’s current action, and only then surfaces guidance plus documentation support.

This replacement plan explicitly includes:
- `CDS Hooks` triggers from the EHR
- `CRD`-style guidance triggers when payer rules become relevant in workflow
- `DTR`-style documentation retrieval for missing evidence collection
- the `x402` payment scaffold for the premium readiness/documentation output
- sponsor-tool usage for `Nimble`, `ClickHouse`, and `Senso`

## Core Product Flow
1. A trusted source registry defines approved payer-policy URLs and source metadata
2. `Nimble` fetches current policy content from those approved sources
3. `ClickHouse` stores prior and current snapshots plus audit events
4. The agent computes diffs and classifies changes as `material`, `non_material`, or `ambiguous`
5. Material changes are stored in an active policy-change watchlist
6. The system listens for `CDS Hooks`-style EHR triggers such as `order-select` and `order-sign`
7. When an EHR trigger arrives, the agent checks whether it aligns with the changed policy, the payer context, and the doctor’s current action
8. If relevant, the system creates a `CRD`-style guidance opportunity
9. If the guidance implies missing documentation or prior-auth evidence needs, the system creates a `DTR`-style documentation retrieval opportunity
10. The premium output is generated through a payment-gated endpoint
11. After payment verification, `Senso` retrieves grounded checklist content and the system assembles the readiness/documentation card
12. All watch, trigger, guidance, documentation, payment, and output events are logged to `ClickHouse`
