from __future__ import annotations

from app.models import ActivePolicyChange, ClinicalTriggerEvent, ReadinessCard, ReadinessChecklist


def build_readiness_card(
    clinical_trigger: ClinicalTriggerEvent,
    policy_change: ActivePolicyChange,
    checklist: ReadinessChecklist,
    payment_transaction_id: str,
) -> ReadinessCard:
    recommendation = (
        f"Surface CRD guidance for {clinical_trigger.procedure} and initiate DTR collection "
        f"before the provider completes the authorization workflow."
    )
    return ReadinessCard(
        trigger_id=clinical_trigger.trigger_id,
        watch_id=policy_change.watch_id,
        payer=clinical_trigger.payer,
        procedure=clinical_trigger.procedure,
        recommendation=recommendation,
        required_evidence=checklist.items,
        grounded_note=checklist.grounded_note,
        payment_transaction_id=payment_transaction_id,
    )

