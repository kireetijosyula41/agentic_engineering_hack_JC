from __future__ import annotations

from app.models import MaterialityResult, PolicyDiff


MATERIAL_KEYWORDS = {
    "prior authorization",
    "authorization",
    "documentation",
    "medical necessity",
    "evidence",
    "clinical notes",
    "conservative therapy",
    "diagnosis",
    "exclusion",
    "approval",
    "requires",
    "step therapy",
    "effective date",
    "notification",
    "coverage",
}

AMBIGUOUS_KEYWORDS = {"may", "consider", "typically", "recommended"}


def classify_materiality(policy_diff: PolicyDiff) -> MaterialityResult:
    text = " ".join(policy_diff.changed_lines).lower()
    if not policy_diff.has_changes:
        return MaterialityResult(
            source_id=policy_diff.source_id,
            classification="non_material",
            confidence=0.99,
            explanation="No content changes were detected.",
            impacted_keywords=[],
        )

    impacted = sorted([keyword for keyword in MATERIAL_KEYWORDS if keyword in text])
    ambiguous = sorted([keyword for keyword in AMBIGUOUS_KEYWORDS if keyword in text])

    if impacted:
        return MaterialityResult(
            source_id=policy_diff.source_id,
            classification="material",
            confidence=0.9,
            explanation="The change affects authorization or documentation requirements.",
            impacted_keywords=impacted,
        )

    if ambiguous:
        return MaterialityResult(
            source_id=policy_diff.source_id,
            classification="ambiguous",
            confidence=0.55,
            explanation="The language changed, but the policy impact is not explicit.",
            impacted_keywords=ambiguous,
        )

    return MaterialityResult(
        source_id=policy_diff.source_id,
        classification="non_material",
        confidence=0.8,
        explanation="The change appears editorial and does not alter requirements.",
        impacted_keywords=[],
    )
