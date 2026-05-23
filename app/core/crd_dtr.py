from __future__ import annotations

from app.models import ActivePolicyChange, CRDDecision, ClinicalTriggerEvent, DTRRequest


def evaluate_crd_opportunity(
    clinical_trigger: ClinicalTriggerEvent,
    active_policy_changes: list[ActivePolicyChange],
) -> CRDDecision:
    normalized_procedure = clinical_trigger.procedure.lower()
    normalized_action = clinical_trigger.provider_action.lower()
    normalized_plan = (clinical_trigger.plan or "").lower()
    trigger_text = " ".join(
        [
            normalized_procedure,
            normalized_action,
            " ".join(code.lower() for code in clinical_trigger.procedure_codes),
        ]
    )

    for change in active_policy_changes:
        policy_text = " ".join(
            [
                " ".join(change.procedure_keywords).lower(),
                " ".join(change.diff.changed_lines).lower(),
                " ".join(change.documentation_required).lower(),
                (change.changed_section or "").lower(),
            ]
        )
        keyword_match = any(keyword.lower() in trigger_text for keyword in change.procedure_keywords)
        if not keyword_match:
            keyword_match = any(token in policy_text for token in clinical_trigger.procedure.lower().split() if len(token) > 3)
        payer_match = change.payer.lower() == clinical_trigger.payer.lower()
        plan_match = not change.plan_type or not normalized_plan or change.plan_type.lower() in normalized_plan
        action_match = "order" in normalized_action or clinical_trigger.hook in {"order-select", "order-sign"}
        dtr_ready = bool(change.documentation_required or change.materiality.impacted_keywords)

        if change.materiality.classification == "ambiguous":
            return CRDDecision(
                trigger_id=clinical_trigger.trigger_id,
                route="human_review",
                aligned=False,
                matched_watch_id=change.watch_id,
                reason="Policy change is ambiguous and requires human review before surfacing guidance.",
            )

        if payer_match and plan_match and keyword_match and action_match and dtr_ready and change.materiality.classification == "material":
            return CRDDecision(
                trigger_id=clinical_trigger.trigger_id,
                route="crd_guidance_ready",
                aligned=True,
                matched_watch_id=change.watch_id,
                reason="Clinical trigger aligns with a watched material policy update and DTR evidence can be gathered now.",
                suggested_next_step="Collect the required documentation before prior authorization submission.",
            )

    return CRDDecision(
        trigger_id=clinical_trigger.trigger_id,
        route="suppress",
        aligned=False,
        reason="No active material policy change aligned with the current doctor action.",
    )


def evaluate_dtr_requirements(
    crd_decision: CRDDecision,
    clinical_trigger: ClinicalTriggerEvent,
    policy_change: ActivePolicyChange,
) -> DTRRequest:
    required_evidence = policy_change.documentation_required or policy_change.materiality.impacted_keywords or [
        "prior authorization requirements",
        "supporting documentation",
    ]
    return DTRRequest(
        trigger_id=clinical_trigger.trigger_id,
        watch_id=policy_change.watch_id,
        payer=clinical_trigger.payer,
        procedure=clinical_trigger.procedure,
        required_evidence=required_evidence,
        documentation_gap="Documentation tied to the detected policy change must be gathered before submission can proceed.",
    )
