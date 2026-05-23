from __future__ import annotations

import uuid

from app.models import PaymentRequirementResult, PaymentVerificationResult


class PaymentAdapter:
    def require_payment(self, request_context: dict) -> PaymentRequirementResult:
        return PaymentRequirementResult(
            required=True,
            status_code=402,
            payment_token_hint="demo-paid",
            message="Payment required. Re-submit with payment_token=demo-paid.",
        )

    def verify_payment(self, payment_payload: dict) -> PaymentVerificationResult:
        token = payment_payload.get("payment_token")
        if token == "demo-paid":
            return PaymentVerificationResult(
                verified=True,
                transaction_id=f"txn-{uuid.uuid4().hex[:12]}",
                message="Mock payment verified.",
            )
        return PaymentVerificationResult(
            verified=False,
            message="Payment token missing or invalid.",
        )

