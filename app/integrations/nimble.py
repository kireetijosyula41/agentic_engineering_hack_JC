from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from app.models import PolicyDocument, TrustedSource


class NimbleAdapter:
    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir
        self.last_error: str | None = None

    def fetch_policy_with_nimble(self, source: TrustedSource, use_baseline: bool = False) -> PolicyDocument:
        if use_baseline:
            return self._fetch_fixture(source, use_baseline=True)

        live_document = self._fetch_live(source)
        if live_document is not None:
            return live_document

        return self._fetch_fixture(source, use_baseline=False)

    def _fetch_fixture(self, source: TrustedSource, use_baseline: bool) -> PolicyDocument:
        fixture_name = source.baseline_fixture if use_baseline else source.latest_fixture
        content = (self.fixtures_dir / fixture_name).read_text(encoding="utf-8")
        return PolicyDocument(
            source_id=source.source_id,
            payer=source.payer,
            url=source.approved_url,
            content=content,
            via="nimble_fixture_fallback",
        )

    def _fetch_live(self, source: TrustedSource) -> PolicyDocument | None:
        if not os.environ.get("NIMBLE_API_KEY"):
            self.last_error = "NIMBLE_API_KEY is not set."
            return None

        try:
            result = subprocess.run(
                [
                    "nimble",
                    "--format",
                    "json",
                    "extract",
                    "--url",
                    source.approved_url,
                    "--format",
                    "markdown",
                    "--request-timeout",
                    "10000",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.CalledProcessError as exc:
            self.last_error = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            return None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.last_error = f"JSONDecodeError: {exc}"
            return None

        markdown = ((payload.get("data") or {}).get("markdown") or "").strip()
        if not markdown:
            self.last_error = "Nimble returned an empty markdown payload."
            return None

        normalized = self._normalize_live_markdown(source, markdown)
        if not normalized.strip():
            self.last_error = "Nimble returned content, but no policy metadata could be extracted."
            return None

        self.last_error = None
        return PolicyDocument(
            source_id=source.source_id,
            payer=source.payer,
            url=source.approved_url,
            content=normalized,
            via="nimble_live_cli",
        )

    def _normalize_live_markdown(self, source: TrustedSource, markdown: str) -> str:
        if "prior-auth-advance-notification" in source.approved_url:
            return self._extract_prior_auth_updates(source, markdown)
        return self._extract_policy_listing(source, markdown)

    def _extract_policy_listing(self, source: TrustedSource, markdown: str) -> str:
        page_title = self._extract_page_title(markdown)
        blocks = re.findall(
            r"#####\s+\[(?P<title>[^\]]+)\]\((?P<pdf_url>https://[^\)]+\.pdf)\)\s+"
            r"Last Published\s+(?P<last_published>\d{2}\.\d{2}\.\d{4})\s+"
            r"Effective Date:\s+(?P<effective_date>\d{2}\.\d{2}\.\d{4})\s+–\s+(?P<body>.*?)(?=\n#####|\Z)",
            markdown,
            flags=re.S,
        )
        records: list[dict[str, Any]] = []
        for title, pdf_url, last_published, effective_date, body in blocks[:40]:
            summary = self._first_sentence(body)
            procedure_codes = self._extract_codes(body, label="Procedure Codes?")
            hcpcs_codes = [code for code in procedure_codes if code.startswith(("J", "Q", "C", "A"))]
            cpt_codes = [code for code in procedure_codes if code[0].isdigit() or code.endswith("T")]
            icd10_codes = self._extract_icd10_codes(body)
            records.append(
                {
                    "policy_title": title.strip(),
                    "policy_type": self._infer_policy_type(title),
                    "effective_date": effective_date,
                    "last_published_date": last_published,
                    "summary_of_changes": summary,
                    "current_cpt_hcpcs_codes": cpt_codes + hcpcs_codes,
                    "added_cpt_hcpcs_codes": cpt_codes + hcpcs_codes,
                    "removed_cpt_hcpcs_codes": [],
                    "current_icd10_codes": icd10_codes,
                    "added_icd10_codes": icd10_codes,
                    "removed_icd10_codes": [],
                    "added_prior_authorization_requirement": self._mentions_prior_auth(body),
                    "removed_prior_authorization_requirement": False,
                    "policy_change_type": "revised/current",
                    "pdf_url": pdf_url,
                    "source_payer": source.payer,
                    "plan_type": source.plan or "Unknown",
                }
            )
        return self._format_records(source, page_title or source.source_id, records)

    def _extract_prior_auth_updates(self, source: TrustedSource, markdown: str) -> str:
        page_title = self._extract_page_title(markdown)
        pattern = re.compile(
            r"(?P<date>[A-Z][a-z]+ \d{2}, \d{4})\s+###\s+(?P<title>.+?)\s+"
            r"\[Read Full Update [^\]]+\]\((?P<url>https://[^\)]+)\)",
            flags=re.S,
        )
        records: list[dict[str, Any]] = []
        for match in pattern.finditer(markdown):
            title = " ".join(match.group("title").split())
            summary = title
            records.append(
                {
                    "policy_title": title,
                    "policy_type": "prior authorization update",
                    "effective_date": match.group("date"),
                    "last_published_date": match.group("date"),
                    "summary_of_changes": summary,
                    "current_cpt_hcpcs_codes": [],
                    "added_cpt_hcpcs_codes": [],
                    "removed_cpt_hcpcs_codes": [],
                    "current_icd10_codes": [],
                    "added_icd10_codes": [],
                    "removed_icd10_codes": [],
                    "added_prior_authorization_requirement": "prior authorization" in title.lower(),
                    "removed_prior_authorization_requirement": "no longer" in title.lower(),
                    "policy_change_type": self._infer_change_type(title),
                    "pdf_url": match.group("url"),
                    "source_payer": source.payer,
                    "plan_type": source.plan or "Unknown",
                }
            )
        return self._format_records(source, page_title or source.source_id, records)

    def _format_records(self, source: TrustedSource, page_title: str, records: list[dict[str, Any]]) -> str:
        header = [
            f"source_id: {source.source_id}",
            f"source_payer: {source.payer}",
            f"plan_type: {source.plan or 'Unknown'}",
            f"approved_url: {source.approved_url}",
            f"page_title: {page_title}",
            f"policies_found: {len(records)}",
        ]
        body = json.dumps(records, indent=2)
        return "\n".join(header) + "\npolicy_records:\n" + body + "\n"

    def _match_first(self, text: str, pattern: str) -> str | None:
        match = re.search(pattern, text)
        return match.group(1).strip() if match else None

    def _extract_page_title(self, markdown: str) -> str:
        for match in re.findall(r"#\s+(.+)", markdown):
            candidate = match.strip()
            lowered = candidate.lower()
            if lowered in {"search", "sign in"}:
                continue
            if any(token in lowered for token in ("unitedhealthcare", "policy", "prior authorization", "notification")):
                return candidate
        for match in re.findall(r"#\s+(.+)", markdown):
            candidate = match.strip()
            if candidate.lower() not in {"search", "sign in"}:
                return candidate
        return "Unknown page"

    def _first_sentence(self, text: str) -> str:
        cleaned = " ".join(text.split())
        if ". " in cleaned:
            return cleaned.split(". ", 1)[0].strip() + "."
        return cleaned[:240]

    def _extract_codes(self, text: str, label: str) -> list[str]:
        pattern = re.compile(label + r":\s*([A-Z0-9,\-\s]+)\.", flags=re.I)
        match = pattern.search(text)
        if not match:
            return []
        raw_codes = re.split(r",\s*", match.group(1).strip())
        return [code.strip() for code in raw_codes if code.strip()]

    def _extract_icd10_codes(self, text: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"\b[A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?\b", text)))

    def _infer_policy_type(self, title: str) -> str:
        lowered = title.lower()
        if "drug policy" in lowered:
            return "medical benefit drug policy"
        if "medical policy" in lowered:
            return "medical policy"
        if "coverage" in lowered:
            return "coverage policy"
        return "policy"

    def _mentions_prior_auth(self, text: str) -> bool:
        lowered = text.lower()
        return "prior authorization" in lowered or "advance notification" in lowered

    def _infer_change_type(self, title: str) -> str:
        lowered = title.lower()
        if "no longer" in lowered or "retire" in lowered:
            return "retired"
        if "new" in lowered:
            return "new"
        if "change" in lowered or "update" in lowered:
            return "revised"
        return "current"

    def integration_status(self) -> dict[str, Any]:
        has_api_key = bool(os.environ.get("NIMBLE_API_KEY"))
        has_project_id = bool(os.environ.get("NIMBLE_PROJECT_ID"))
        live_capable = has_api_key
        status = "ready_for_live_fetch" if live_capable else "fallback_only"
        return {
            "service": "nimble",
            "configured": has_api_key,
            "live_enabled": live_capable,
            "status": status,
            "failure_reason": self.last_error,
            "details": {
                "has_api_key": has_api_key,
                "has_project_id": has_project_id,
                "fixtures_dir": str(self.fixtures_dir),
            },
        }
