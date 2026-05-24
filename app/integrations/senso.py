from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from app.models import ActivePolicyChange, ClinicalTriggerEvent, ReadinessCard, ReadinessChecklist, SensoPublishResult


class SensoAdapter:
    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir
        self.last_error: str | None = None

    @property
    def cited_md_publisher_id(self) -> str:
        return os.environ.get("SENSO_CITED_MD_PUBLISHER_ID", "")

    def query_senso_checklist(self, payer: str, procedure: str, policy_context: dict) -> ReadinessChecklist:
        live_checklist = self._query_live(payer=payer, procedure=procedure)
        if live_checklist is not None:
            return live_checklist

        payload = json.loads((self.fixtures_dir / "senso_checklist_example.json").read_text(encoding="utf-8"))
        return ReadinessChecklist(
            payer=payer,
            procedure=procedure,
            items=payload["items"],
            source="senso_fixture_fallback",
            grounded_note=payload["grounded_note"],
        )

    def publish_record_to_cited_md(
        self,
        clinical_trigger: ClinicalTriggerEvent,
        policy_change: ActivePolicyChange,
        checklist: ReadinessChecklist,
        card: ReadinessCard,
    ) -> SensoPublishResult:
        publisher_id = self.cited_md_publisher_id
        if not os.environ.get("SENSO_API_KEY"):
            return SensoPublishResult(
                success=False,
                publisher_id=publisher_id,
                message="SENSO_API_KEY is not set.",
            )
        if not publisher_id:
            return SensoPublishResult(
                success=False,
                publisher_id="",
                message="SENSO_CITED_MD_PUBLISHER_ID is not set.",
            )

        generation_result = self._run_senso_command(
            [
                "generate",
                "update-settings",
                "--data",
                json.dumps({"enable_content_generation": True}),
            ]
        )
        if not generation_result["success"]:
            return SensoPublishResult(
                success=False,
                publisher_id=publisher_id,
                message=f"Failed to enable Senso content generation: {generation_result['message']}",
                raw_response=generation_result.get("raw_response", {}),
            )

        question_text = (
            f"What documentation is required for {clinical_trigger.payer} "
            f"{clinical_trigger.procedure} after policy change {policy_change.watch_id}?"
        )
        question_payload = {
            "question_text": question_text,
            "type": "decision",
            "tag_ids": [],
        }
        question_result = self._run_senso_command(
            [
                "questions",
                "create",
                "--data",
                json.dumps(question_payload),
            ]
        )
        if not question_result["success"]:
            return SensoPublishResult(
                success=False,
                publisher_id=publisher_id,
                message=f"Failed to create Senso question: {question_result['message']}",
                raw_response=question_result.get("raw_response", {}),
            )

        question_id = self._extract_first_id(question_result["raw_response"], ["question_id", "geo_question_id", "id"])
        if not question_id:
            return SensoPublishResult(
                success=False,
                publisher_id=publisher_id,
                message="Senso question was created, but no question ID was returned.",
                raw_response=question_result["raw_response"],
            )

        markdown = self._build_publish_markdown(clinical_trigger, policy_change, checklist, card)
        publish_payload = {
            "geo_question_id": question_id,
            "raw_markdown": markdown,
            "seo_title": f"{clinical_trigger.payer} {clinical_trigger.procedure} readiness card",
            "summary": policy_change.diff.summary,
            "publisher_ids": [publisher_id],
        }
        publish_result = self._run_senso_command(
            [
                "engine",
                "publish",
                "--data",
                json.dumps(publish_payload),
                "--publisher-ids",
                publisher_id,
            ]
        )
        if not publish_result["success"]:
            return SensoPublishResult(
                success=False,
                publisher_id=publisher_id,
                question_id=question_id,
                message=f"Failed to publish to cited.md: {publish_result['message']}",
                raw_response=publish_result.get("raw_response", {}),
            )

        raw_response = publish_result["raw_response"]
        published_url = self._extract_first_value(raw_response, ["url", "published_url", "display_url"])
        if not published_url:
            destinations = raw_response.get("publish_destinations") or []
            if destinations:
                published_url = destinations[0].get("display_url")
        return SensoPublishResult(
            success=True,
            publisher_id=publisher_id,
            question_id=question_id,
            content_id=self._extract_first_id(raw_response, ["content_id", "contentId", "id"]),
            publish_record_id=self._extract_first_id(raw_response, ["publish_record_id", "publishRecordId"]),
            url=published_url,
            message="Published readiness output to cited.md.",
            raw_response=raw_response,
        )

    def _query_live(self, payer: str, procedure: str) -> ReadinessChecklist | None:
        if not os.environ.get("SENSO_API_KEY"):
            self.last_error = "SENSO_API_KEY is not set."
            return None

        query = f"{payer} {procedure} readiness requirements"
        try:
            result = subprocess.run(
                [
                    "senso",
                    "search",
                    "context",
                    query,
                    "--output",
                    "json",
                    "--quiet",
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

        results = payload.get("results") or []
        if not results:
            self.last_error = "Senso search returned no results. The knowledge base appears empty for this query."
            return None

        items = self._extract_items(results)
        if not items:
            self.last_error = "Senso returned results, but no checklist-like items could be extracted."
            return None

        self.last_error = None
        return ReadinessChecklist(
            payer=payer,
            procedure=procedure,
            items=items,
            source="senso_live_search",
            grounded_note=f"Checklist derived from Senso search results for query: {query}",
        )

    def _extract_items(self, results: list[dict[str, Any]]) -> list[str]:
        items: list[str] = []
        for result in results:
            for key in ("text", "content", "chunk_text", "snippet"):
                value = result.get(key)
                if isinstance(value, str):
                    items.extend(self._extract_items_from_text(value))
        return items[:5]

    def _extract_items_from_text(self, text: str) -> list[str]:
        extracted: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip(" -*\t")
            if not line:
                continue
            lowered = line.lower()
            if any(
                token in lowered
                for token in (
                    "documentation",
                    "clinical",
                    "diagnosis",
                    "therapy",
                    "order",
                    "imaging",
                    "medical necessity",
                    "evidence",
                    "prior authorization",
                )
            ):
                extracted.append(line[:200])
        return list(dict.fromkeys(extracted))

    def integration_status(self) -> dict[str, Any]:
        has_api_key = bool(os.environ.get("SENSO_API_KEY"))
        has_kb_id = bool(os.environ.get("SENSO_KB_ID"))
        live_capable = has_api_key
        status = "ready_for_live_search" if live_capable else "fallback_only"
        return {
            "service": "senso",
            "configured": has_api_key,
            "live_enabled": live_capable,
            "status": status,
            "failure_reason": self.last_error,
            "details": {
                "has_api_key": has_api_key,
                "has_kb_id": has_kb_id,
                "fixtures_dir": str(self.fixtures_dir),
            },
        }

    def _run_senso_command(self, args: list[str]) -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["senso", "--output", "json", "--quiet", *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            return {
                "success": False,
                "message": exc.stderr.strip() or exc.stdout.strip() or str(exc),
                "raw_response": {},
            }
        except Exception as exc:
            return {
                "success": False,
                "message": f"{type(exc).__name__}: {exc}",
                "raw_response": {},
            }

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", result.stdout, flags=re.S)
            if match:
                try:
                    payload = json.loads(match.group(1))
                except json.JSONDecodeError:
                    payload = {"raw_output": result.stdout}
            else:
                payload = {"raw_output": result.stdout}
        return {
            "success": True,
            "message": "ok",
            "raw_response": payload,
        }

    def _extract_first_id(self, payload: dict[str, Any], keys: list[str]) -> str | None:
        value = self._extract_first_value(payload, keys)
        return str(value) if value else None

    def _extract_first_value(self, payload: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            if key in payload and payload[key]:
                return payload[key]
        return None

    def _build_publish_markdown(
        self,
        clinical_trigger: ClinicalTriggerEvent,
        policy_change: ActivePolicyChange,
        checklist: ReadinessChecklist,
        card: ReadinessCard,
    ) -> str:
        documentation = checklist.items or policy_change.documentation_required
        documentation_lines = "\n".join(f"- {item}" for item in documentation)
        return "\n".join(
            [
                f"# {clinical_trigger.payer} readiness card for {clinical_trigger.procedure}",
                "",
                f"**Payer:** {clinical_trigger.payer}",
                f"**Plan type:** {clinical_trigger.plan or policy_change.plan_type or 'Unknown'}",
                f"**Policy source:** {policy_change.source_url or 'Unknown'}",
                f"**Changed section:** {policy_change.changed_section or 'Policy monitor'}",
                f"**Materiality:** {policy_change.materiality.classification}",
                f"**Change type:** {policy_change.change_type}",
                f"**Effective date:** {policy_change.effective_date or 'Unknown'}",
                "",
                "## Recommendation",
                card.recommendation,
                "",
                "## Documentation required",
                documentation_lines or "- Supporting documentation required",
                "",
                "## Grounded note",
                checklist.grounded_note,
                "",
                "Powered by Senso",
            ]
        )
