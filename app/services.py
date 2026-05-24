from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from app.core.cds import ingest_cds_hook
from app.core.classifier import classify_materiality
from app.core.crd_dtr import evaluate_crd_opportunity, evaluate_dtr_requirements
from app.core.diffing import compute_policy_diff, hash_content
from app.core.readiness import build_readiness_card
from app.integrations.clickhouse_store import ClickHouseStore
from app.integrations.nimble import NimbleAdapter
from app.integrations.payment import PaymentAdapter
from app.integrations.senso import SensoAdapter
from app.models import (
    ActivePolicyChange,
    AgentEvent,
    CRDDecision,
    ClinicalTriggerEvent,
    DTRRequest,
    PolicyChangeRecord,
    PolicySnapshot,
    TrustedSource,
    utc_now,
)


class PolicyPulseService:
    def __init__(self, fixtures_dir: Path, data_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir
        self.data_dir = data_dir
        self.store = ClickHouseStore(data_dir=data_dir)
        self.nimble = NimbleAdapter(fixtures_dir=fixtures_dir)
        self.senso = SensoAdapter(fixtures_dir=fixtures_dir)
        self.payment = PaymentAdapter()
        self.active_changes_path = data_dir / "active_policy_changes.json"
        self.scan_interval_seconds = 15
        self._scan_lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop_event = threading.Event()
        initial_sources = self._read_sources()
        self._monitor_state_lock = threading.Lock()
        self._monitor_state: dict[str, Any] = {
            "mode": "on_demand_until_ui_starts_autonomous_monitor",
            "monitor_running": False,
            "scan_in_progress": False,
            "sources_monitored": len(initial_sources),
            "source_ids": [source.source_id for source in initial_sources],
            "monitored_source_ids": [source.source_id for source in initial_sources],
            "last_scan_started_at": None,
            "last_scan_completed_at": None,
            "last_result": None,
            "latest_materiality": None,
            "latest_change_summary": None,
        }

    def _read_sources(self) -> list[TrustedSource]:
        payload = json.loads((self.fixtures_dir / "trusted_sources.json").read_text(encoding="utf-8"))
        return [TrustedSource(**item) for item in payload]

    def _save_active_changes(self, changes: list[ActivePolicyChange]) -> None:
        self.active_changes_path.write_text(
            json.dumps([change.model_dump(mode="json") for change in changes], indent=2),
            encoding="utf-8",
        )

    def get_active_policy_changes(self) -> list[ActivePolicyChange]:
        recent_policy_changes = self.store.get_recent_policy_changes(limit=100)
        if recent_policy_changes:
            changes = [ActivePolicyChange(**item["payload"]) if "payload" in item else ActivePolicyChange(**item) for item in recent_policy_changes]
            return [change for change in changes if change.materiality.classification in {"material", "ambiguous"}]
        if not self.active_changes_path.exists():
            return []
        payload = json.loads(self.active_changes_path.read_text(encoding="utf-8"))
        return [ActivePolicyChange(**item) for item in payload]

    def get_displayed_policy_change(self) -> ActivePolicyChange | None:
        monitored_source_ids = list(self._monitor_state.get("monitored_source_ids") or [])
        selected_source_id = monitored_source_ids[0] if monitored_source_ids else self.get_default_source_id()
        if not selected_source_id:
            return None
        view = self.get_policy_monitor_view(selected_source_id)
        policy_change = view.get("policy_change")
        if not policy_change:
            return None
        payload = policy_change.get("payload", policy_change)
        try:
            return ActivePolicyChange(**payload)
        except Exception:
            return None

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.store.log_event(AgentEvent(event_type=event_type, payload=payload))

    def get_integration_status(self) -> dict[str, Any]:
        return {
            "clickhouse": self.store.integration_status(),
            "nimble": self.nimble.integration_status(),
            "senso": self.senso.integration_status(),
        }

    def get_recent_policy_changes(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.get_recent_policy_changes(limit=limit)

    def get_default_source_id(self) -> str | None:
        sources = self._read_sources()
        return sources[0].source_id if sources else None

    def get_source_by_id(self, source_id: str | None) -> TrustedSource | None:
        if not source_id:
            return None
        for source in self._read_sources():
            if source.source_id == source_id:
                return source
        return None

    def get_policy_monitor_view(self, source_id: str | None = None) -> dict[str, Any]:
        selected_source_id = source_id or self.get_default_source_id()
        source = self.get_source_by_id(selected_source_id)
        recent_changes = self.get_recent_policy_changes(limit=100)
        latest_change = None
        latest_material_change = None
        for change in recent_changes:
            payload = change.get("payload", change)
            change_source_id = payload.get("source_id") or change.get("policy_id")
            if change_source_id == selected_source_id:
                if latest_change is None:
                    latest_change = change
                materiality = payload.get("materiality", {})
                classification = materiality.get("classification") if isinstance(materiality, dict) else payload.get("materiality")
                if classification in {"material", "ambiguous"}:
                    latest_material_change = change
                    break
        selected_change = latest_material_change or latest_change
        selected_payload = selected_change.get("payload", selected_change) if selected_change else {}
        snapshot_source_id = selected_payload.get("source_id") or selected_source_id
        snapshots = self.store.get_recent_snapshots_for_source(source_id=snapshot_source_id, limit=2) if snapshot_source_id else []
        current_snapshot = snapshots[0] if snapshots else None
        previous_snapshot = snapshots[1] if len(snapshots) > 1 else None
        return {
            "policy_change": selected_change,
            "current_snapshot": current_snapshot,
            "previous_snapshot": previous_snapshot,
            "selected_source_id": selected_source_id,
            "source_url": source.approved_url if source else None,
        }

    def get_monitor_status(self) -> dict[str, Any]:
        with self._monitor_state_lock:
            state = dict(self._monitor_state)
        sources = self._read_sources()
        recent_changes = self.get_recent_policy_changes(limit=max(len(sources), 1))
        latest_change = recent_changes[0] if recent_changes else None
        latest_payload = latest_change.get("payload", latest_change) if latest_change else {}
        if not state.get("sources_monitored"):
            state["sources_monitored"] = len(sources)
            state["source_ids"] = [source.source_id for source in sources]
        if not state.get("latest_materiality"):
            state["latest_materiality"] = (
                latest_payload.get("materiality", {}).get("classification")
                if isinstance(latest_payload.get("materiality"), dict)
                else latest_payload.get("materiality")
            )
        if not state.get("latest_change_summary"):
            state["latest_change_summary"] = latest_change.get("summary") if latest_change else None
        return {
            "mode": state.get("mode"),
            "monitor_running": state.get("monitor_running", False),
            "scan_in_progress": state.get("scan_in_progress", False),
            "sources_monitored": state.get("sources_monitored", len(sources)),
            "source_ids": state.get("source_ids", [source.source_id for source in sources]),
            "monitored_source_ids": state.get("monitored_source_ids", [source.source_id for source in sources]),
            "last_scan_started_at": state.get("last_scan_started_at"),
            "last_scan_completed_at": state.get("last_scan_completed_at"),
            "last_result": state.get("last_result") or ("changes_detected" if recent_changes else "scan_pending"),
            "latest_materiality": state.get("latest_materiality"),
            "latest_change_summary": state.get("latest_change_summary"),
            "scan_interval_seconds": self.scan_interval_seconds,
        }

    def start_autonomous_monitor(self, source_id: str | None = None) -> dict[str, Any]:
        monitored_source_ids = [source_id] if source_id else [source.source_id for source in self._read_sources()]
        if source_id:
            source = self.get_source_by_id(source_id)
            if source is not None:
                self._ensure_demo_material_change(source)
        should_start = False
        with self._monitor_state_lock:
            already_running = self._monitor_thread is not None and self._monitor_thread.is_alive()
            self._monitor_state["monitored_source_ids"] = monitored_source_ids
            self._monitor_state["sources_monitored"] = len(monitored_source_ids)
            self._monitor_state["source_ids"] = monitored_source_ids
            if not already_running:
                self._monitor_stop_event.clear()
                self._monitor_state["mode"] = "autonomous_background_monitor"
                self._monitor_state["monitor_running"] = True
                should_start = True
        if should_start:
            self._monitor_thread = threading.Thread(target=self._monitor_loop, name="policypulse-monitor", daemon=True)
            self._monitor_thread.start()
        return self.get_monitor_status()

    def _ensure_demo_material_change(self, source: TrustedSource) -> None:
        recent_changes = self.get_recent_policy_changes(limit=100)
        for change in recent_changes:
            payload = change.get("payload", change)
            if (payload.get("source_id") or change.get("policy_id")) != source.source_id:
                continue
            materiality = payload.get("materiality", {})
            classification = materiality.get("classification") if isinstance(materiality, dict) else payload.get("materiality")
            if classification in {"material", "ambiguous"}:
                return

        baseline = self.nimble._fetch_fixture(source, use_baseline=True)
        demo_latest = self.nimble._fetch_fixture(source, use_baseline=False)
        previous_snapshot = PolicySnapshot(
            source_id=source.source_id,
            payer=source.payer,
            content_hash=hash_content(baseline.content),
            normalized_content=baseline.content,
            via=baseline.via,
        )
        current_snapshot = PolicySnapshot(
            source_id=source.source_id,
            payer=source.payer,
            content_hash=hash_content(demo_latest.content),
            normalized_content=demo_latest.content,
            via=demo_latest.via,
        )
        self.store.save_snapshot(previous_snapshot)
        self.store.save_snapshot(current_snapshot)
        self.log_event("policy_snapshot_seeded", previous_snapshot.model_dump(mode="json"))
        self.log_event("policy_snapshot_saved", current_snapshot.model_dump(mode="json"))

        # If Nimble can fetch the live page, save it as the authoritative current snapshot
        # so the background monitor compares live→live on subsequent scans and finds no change.
        # Without this, the monitor would diff fixture→live and overwrite the meaningful MRI diff.
        live_doc = self.nimble._fetch_live(source)
        if live_doc is not None:
            live_snapshot = PolicySnapshot(
                source_id=source.source_id,
                payer=source.payer,
                content_hash=hash_content(live_doc.content),
                normalized_content=live_doc.content,
                via=live_doc.via,
            )
            self.store.save_snapshot(live_snapshot)
            self.log_event("policy_snapshot_saved", live_snapshot.model_dump(mode="json"))

        diff = compute_policy_diff(
            source_id=source.source_id,
            payer=source.payer,
            previous_content=baseline.content,
            current_content=demo_latest.content,
        )
        materiality = classify_materiality(diff)
        active_change = ActivePolicyChange(
            watch_id=f"watch-{uuid.uuid4().hex[:10]}",
            source_id=source.source_id,
            payer=source.payer,
            plan_type=source.plan,
            source_url=source.approved_url,
            procedure_keywords=source.procedure_keywords,
            diff=diff,
            materiality=materiality,
            effective_date=self._extract_effective_date(demo_latest.content),
            change_type=self._infer_change_type(diff.changed_lines),
            documentation_required=self._extract_documentation_requirements(diff.changed_lines),
            changed_section=self._extract_changed_section(demo_latest.content),
        )
        change_record = self._build_policy_change_record(active_change)
        self.store.save_policy_change(change_record)
        self.log_event("policy_diff_generated", diff.model_dump(mode="json"))
        self.log_event("materiality_classified", materiality.model_dump(mode="json"))
        self.log_event("policy_change_recorded", change_record.model_dump(mode="json"))
        current_active_changes = self.get_active_policy_changes()
        self._save_active_changes(current_active_changes)

    def _monitor_loop(self) -> None:
        while not self._monitor_stop_event.is_set():
            try:
                with self._monitor_state_lock:
                    monitored_source_ids = list(self._monitor_state.get("monitored_source_ids") or [])
                self.scan_sources(scan_origin="autonomous", source_ids=monitored_source_ids)
            except Exception as exc:
                self.log_event("monitor_scan_failed", {"error": f"{type(exc).__name__}: {exc}"})
                with self._monitor_state_lock:
                    self._monitor_state["last_result"] = "scan_failed"
                    self._monitor_state["scan_in_progress"] = False
            if self._monitor_stop_event.wait(self.scan_interval_seconds):
                break

    def scan_sources(self, scan_origin: str = "manual", source_ids: list[str] | None = None) -> list[ActivePolicyChange]:
        if not self._scan_lock.acquire(blocking=False):
            return self.get_active_policy_changes()
        configured_sources = self._read_sources()
        selected_ids = set(source_ids or [source.source_id for source in configured_sources])
        sources = [source for source in configured_sources if source.source_id in selected_ids]
        with self._monitor_state_lock:
            self._monitor_state["mode"] = (
                "autonomous_background_monitor" if scan_origin == "autonomous" else "manual_rescan_with_background_monitor"
            )
            self._monitor_state["monitor_running"] = True
            self._monitor_state["scan_in_progress"] = True
            self._monitor_state["sources_monitored"] = len(sources)
            self._monitor_state["source_ids"] = [source.source_id for source in sources]
            self._monitor_state["monitored_source_ids"] = [source.source_id for source in sources]
            self._monitor_state["last_scan_started_at"] = self._now_iso()
        self.log_event("monitor_scan_started", {"origin": scan_origin, "sources_monitored": len(sources)})
        active_changes: list[ActivePolicyChange] = []
        changed_sources = 0
        unchanged_sources = 0
        latest_materiality: str | None = None
        latest_summary: str | None = None
        try:
            for source in sources:
                self.log_event(
                    "nimble_fetch_started",
                    {"source_id": source.source_id, "payer": source.payer, "url": source.approved_url, "origin": scan_origin},
                )
                latest = self.nimble.fetch_policy_with_nimble(source, use_baseline=False)
                self.log_event(
                    "nimble_fetch_completed",
                    {"source_id": source.source_id, "payer": source.payer, "via": latest.via, "origin": scan_origin},
                )
                previous_snapshot_payload = self.store.get_latest_snapshot_for_source(source.source_id)
                if previous_snapshot_payload:
                    previous_content = previous_snapshot_payload["normalized_content"]
                    previous_hash = previous_snapshot_payload["content_hash"]
                else:
                    baseline = self.nimble.fetch_policy_with_nimble(source, use_baseline=True)
                    previous_content = baseline.content
                    previous_hash = hash_content(previous_content)
                    previous_snapshot = PolicySnapshot(
                        source_id=source.source_id,
                        payer=source.payer,
                        content_hash=previous_hash,
                        normalized_content=previous_content,
                        via=baseline.via,
                    )
                    self.store.save_snapshot(previous_snapshot)
                    self.log_event("policy_snapshot_seeded", previous_snapshot.model_dump(mode="json"))

                current_hash = hash_content(latest.content)
                if previous_hash == current_hash:
                    unchanged_sources += 1
                    self.log_event(
                        "policy_scan_no_change",
                        {"source_id": source.source_id, "payer": source.payer, "content_hash": current_hash, "origin": scan_origin},
                    )
                    continue

                changed_sources += 1
                current_snapshot = PolicySnapshot(
                    source_id=source.source_id,
                    payer=source.payer,
                    content_hash=current_hash,
                    normalized_content=latest.content,
                    via=latest.via,
                )
                self.store.save_snapshot(current_snapshot)
                self.log_event("policy_snapshot_saved", current_snapshot.model_dump(mode="json"))

                diff = compute_policy_diff(
                    source_id=source.source_id,
                    payer=source.payer,
                    previous_content=previous_content,
                    current_content=latest.content,
                )
                self.log_event("policy_diff_generated", diff.model_dump(mode="json"))
                materiality = classify_materiality(diff)
                self.log_event("materiality_classified", materiality.model_dump(mode="json"))
                active_change = ActivePolicyChange(
                    watch_id=f"watch-{uuid.uuid4().hex[:10]}",
                    source_id=source.source_id,
                    payer=source.payer,
                    plan_type=source.plan,
                    source_url=source.approved_url,
                    procedure_keywords=source.procedure_keywords,
                    diff=diff,
                    materiality=materiality,
                    effective_date=self._extract_effective_date(latest.content),
                    change_type=self._infer_change_type(diff.changed_lines),
                    documentation_required=self._extract_documentation_requirements(diff.changed_lines),
                    changed_section=self._extract_changed_section(latest.content),
                )
                if materiality.classification in {"material", "ambiguous"}:
                    change_record = self._build_policy_change_record(active_change)
                    self.store.save_policy_change(change_record)
                    self.log_event("policy_change_recorded", change_record.model_dump(mode="json"))
                    active_changes.append(active_change)
                else:
                    self.log_event("policy_change_non_material", {"source_id": source.source_id, "summary": diff.summary})
                latest_materiality = materiality.classification
                latest_summary = diff.summary

            # Merge new material changes with existing watchlist so non_material
            # scans don't wipe out previously detected material changes.
            existing_active = self.get_active_policy_changes()
            new_source_ids = {c.source_id for c in active_changes}
            merged_active = [c for c in existing_active if c.source_id not in new_source_ids] + active_changes
            self._save_active_changes(merged_active)
            result = "changes_detected" if changed_sources else "no_changes_detected"
            self.log_event(
                "monitor_scan_completed",
                {
                    "origin": scan_origin,
                    "sources_monitored": len(sources),
                    "changed_sources": changed_sources,
                    "unchanged_sources": unchanged_sources,
                    "result": result,
                },
            )
            with self._monitor_state_lock:
                self._monitor_state["scan_in_progress"] = False
                self._monitor_state["last_scan_completed_at"] = self._now_iso()
                self._monitor_state["last_result"] = result
                if latest_materiality:
                    self._monitor_state["latest_materiality"] = latest_materiality
                if latest_summary:
                    self._monitor_state["latest_change_summary"] = latest_summary
            return all_active_changes
        finally:
            with self._monitor_state_lock:
                self._monitor_state["scan_in_progress"] = False
            self._scan_lock.release()

    def ingest_cds_hook(self, payload: dict[str, Any]) -> ClinicalTriggerEvent:
        trigger = ingest_cds_hook(payload)
        self.log_event("cds_hook_received", {"trigger_id": trigger.trigger_id, "hook": trigger.hook})
        self.store.save_ehr_event(trigger)
        self.log_event("ehr_event_recorded", trigger.model_dump(mode="json"))
        if trigger.hook == "order-select":
            self.log_event("cds_order_select_received", trigger.model_dump(mode="json"))
        elif trigger.hook == "order-sign":
            self.log_event("cds_order_sign_received", trigger.model_dump(mode="json"))
        return trigger

    def evaluate_crd(self, trigger: ClinicalTriggerEvent) -> CRDDecision:
        displayed_change = self.get_displayed_policy_change()
        candidate_changes = [displayed_change] if displayed_change is not None else []
        if not candidate_changes:
            candidate_changes = self.get_active_policy_changes()
        decision = evaluate_crd_opportunity(trigger, candidate_changes)
        self.log_event("crd_evaluated", decision.model_dump(mode="json"))
        if decision.route == "crd_guidance_ready":
            self.log_event("crd_guidance_ready", decision.model_dump(mode="json"))
        elif decision.route == "suppress":
            self.log_event("crd_suppressed", decision.model_dump(mode="json"))
        return decision

    def evaluate_dtr(self, trigger: ClinicalTriggerEvent, crd_decision: CRDDecision) -> DTRRequest:
        policy_change = self._find_policy_change(crd_decision.matched_watch_id)
        dtr_request = evaluate_dtr_requirements(crd_decision, trigger, policy_change)
        self.log_event("dtr_requested", dtr_request.model_dump(mode="json"))
        self.log_event("dtr_requirements_identified", dtr_request.model_dump(mode="json"))
        return dtr_request

    def require_payment(self, request_context: dict[str, Any]) -> dict[str, Any]:
        result = self.payment.require_payment(request_context).model_dump(mode="json")
        self.log_event("payment_required", result)
        return result

    def generate_readiness_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        verification = self.payment.verify_payment(payload)
        if not verification.verified:
            failure = verification.model_dump(mode="json")
            self.log_event("payment_failed", failure)
            return {"status": "payment_failed", "payment": failure}

        self.log_event("payment_verified", verification.model_dump(mode="json"))
        trigger = ClinicalTriggerEvent(**payload["clinical_trigger"])
        crd_decision = CRDDecision(**payload["crd_decision"])
        policy_change = self._find_policy_change(crd_decision.matched_watch_id)
        self.log_event("senso_query_started", {"trigger_id": trigger.trigger_id, "watch_id": policy_change.watch_id})
        checklist = self.senso.query_senso_checklist(
            payer=trigger.payer,
            procedure=trigger.procedure,
            policy_context={"watch_id": policy_change.watch_id},
        )
        self.log_event("senso_checklist_retrieved", checklist.model_dump(mode="json"))
        card = build_readiness_card(
            clinical_trigger=trigger,
            policy_change=policy_change,
            checklist=checklist,
            payment_transaction_id=verification.transaction_id or "unknown",
        )
        publish_result = self.senso.publish_record_to_cited_md(
            clinical_trigger=trigger,
            policy_change=policy_change,
            checklist=checklist,
            card=card,
        )
        self.log_event("senso_publish_completed" if publish_result.success else "senso_publish_failed", publish_result.model_dump(mode="json"))
        self.log_event("readiness_card_generated", card.model_dump(mode="json"))
        return {
            "status": "ok",
            "payment": verification.model_dump(mode="json"),
            "checklist": checklist.model_dump(mode="json"),
            "card": card.model_dump(mode="json"),
            "publication": publish_result.model_dump(mode="json"),
        }

    def _find_policy_change(self, watch_id: str | None) -> ActivePolicyChange:
        displayed_change = self.get_displayed_policy_change()
        if displayed_change is not None and displayed_change.watch_id == watch_id:
            return displayed_change
        for item in self.get_active_policy_changes():
            if item.watch_id == watch_id:
                return item
        raise ValueError("Matching active policy change was not found.")

    def _build_policy_change_record(self, active_change: ActivePolicyChange) -> PolicyChangeRecord:
        return PolicyChangeRecord(
            change_id=f"change-{uuid.uuid4().hex[:10]}",
            watch_id=active_change.watch_id,
            policy_id=active_change.source_id,
            payer=active_change.payer,
            plan_type=active_change.plan_type,
            effective_date=active_change.effective_date,
            change_type=active_change.change_type,
            materiality=active_change.materiality.classification,
            summary=active_change.diff.summary,
            documentation_required=active_change.documentation_required,
            source_url=active_change.source_url or "",
            diff_text="\n".join(active_change.diff.changed_lines),
            payload=active_change.model_dump(mode="json"),
        )

    def _extract_effective_date(self, content: str) -> str | None:
        match = re.search(r"Effective Date:\s*([A-Za-z0-9\.\-, ]+)", content, flags=re.I)
        return match.group(1).strip() if match else None

    def _extract_documentation_requirements(self, changed_lines: list[str]) -> list[str]:
        requirements: list[str] = []
        for line in changed_lines:
            cleaned = line[1:].strip()
            if ":" in cleaned and cleaned.lstrip().startswith('"'):
                cleaned = cleaned.split(":", 1)[1].strip().strip('",')
            normalized = self._normalize_documentation_requirement(cleaned.replace("\\n", " ").strip())
            if normalized:
                requirements.append(normalized)
        return list(dict.fromkeys(requirements))[:6]

    def _normalize_documentation_requirement(self, text: str) -> str | None:
        lowered = text.lower()
        mappings = [
            (("prior authorization", "advance notification"), "Prior authorization required"),
            (("clinical notes",), "Clinical notes"),
            (("medical necessity",), "Medical necessity documentation"),
            (("step therapy", "failed"), "Failed step therapy documentation"),
            (("conservative therapy",), "Conservative therapy history"),
            (("prior imaging",), "Prior imaging evidence"),
            (("diagnosis",), "Diagnosis supporting medical necessity"),
            (("severity", "score"), "Disease severity score"),
            (("history", "biologic"), "Prior therapy history"),
            (("documentation",), "Supporting clinical documentation"),
            (("evidence",), "Supporting clinical evidence"),
        ]
        for tokens, label in mappings:
            if all(token in lowered for token in tokens):
                return label
        if any(
            token in lowered
            for token in ("documentation", "clinical", "diagnosis", "therapy", "evidence", "medical necessity")
        ):
            cleaned = text.strip(" -,.")
            return cleaned[:140] if cleaned else None
        return None

    def _infer_change_type(self, changed_lines: list[str]) -> str:
        diff_text = " ".join(changed_lines).lower()
        if "prior authorization" in diff_text or "advance notification" in diff_text:
            return "prior_authorization_requirement_change"
        if "effective date" in diff_text:
            return "effective_date_change"
        if "documentation" in diff_text or "medical necessity" in diff_text:
            return "documentation_requirement_change"
        return "policy_revision"

    def _extract_changed_section(self, content: str) -> str | None:
        match = re.search(r"page_title:\s*(.+)", content)
        return match.group(1).strip() if match else None

    def _now_iso(self) -> str:
        return utc_now().isoformat()
