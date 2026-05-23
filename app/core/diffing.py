from __future__ import annotations

import hashlib
from difflib import unified_diff

from app.models import PolicyDiff


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_policy_diff(
    source_id: str,
    payer: str,
    previous_content: str,
    current_content: str,
) -> PolicyDiff:
    previous_hash = hash_content(previous_content)
    current_hash = hash_content(current_content)
    lines = list(
        unified_diff(
            previous_content.splitlines(),
            current_content.splitlines(),
            lineterm="",
        )
    )
    changed_lines = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    return PolicyDiff(
        source_id=source_id,
        payer=payer,
        previous_hash=previous_hash,
        current_hash=current_hash,
        changed_lines=changed_lines,
        summary=f"{len(changed_lines)} changed policy lines detected.",
        has_changes=previous_hash != current_hash,
    )

