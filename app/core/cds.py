from __future__ import annotations

import uuid
from typing import Any

from app.models import ClinicalTriggerEvent


def ingest_cds_hook(hook_payload: dict[str, Any]) -> ClinicalTriggerEvent:
    hook = hook_payload.get("hook")
    context = hook_payload.get("context", {})
    return ClinicalTriggerEvent(
        trigger_id=hook_payload.get("trigger_id", str(uuid.uuid4())),
        hook=hook,
        payer=context.get("payer", "Unknown"),
        plan=context.get("plan"),
        procedure=context.get("procedure", "Unknown procedure"),
        procedure_codes=context.get("procedure_codes", []),
        provider_action=context.get("provider_action", "unspecified"),
        patient_context=context.get("patient_context", {}),
        raw_payload=hook_payload,
    )

