from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import clickhouse_connect
except Exception:  # pragma: no cover - local fallback keeps the demo running
    clickhouse_connect = None

from app.models import AgentEvent, ClinicalTriggerEvent, PolicyChangeRecord, PolicySnapshot


class ClickHouseStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.data_dir / "agent_events.jsonl"
        self.snapshots_file = self.data_dir / "policy_snapshots.jsonl"
        self.policy_changes_file = self.data_dir / "policy_changes.jsonl"
        self.ehr_events_file = self.data_dir / "ehr_events.jsonl"
        self.last_connection_error: str | None = None
        self.client = self._build_client()

    def _build_client(self):
        try:
            host = os.environ.get("CLICKHOUSE_HOST")
            if not host or clickhouse_connect is None:
                if not host:
                    self.last_connection_error = "CLICKHOUSE_HOST is not set."
                elif clickhouse_connect is None:
                    self.last_connection_error = "clickhouse_connect is not installed."
                return None
            client = clickhouse_connect.get_client(
                host=host,
                port=int(os.environ.get("CLICKHOUSE_PORT", "8443")),
                username=os.environ.get("CLICKHOUSE_USER", "default"),
                password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
                database=os.environ.get("CLICKHOUSE_DATABASE", "default"),
                secure=True,
            )
            self._ensure_tables(client)
            self.last_connection_error = None
            return client
        except Exception as exc:
            self.last_connection_error = f"{type(exc).__name__}: {exc}"
            return None

    def _ensure_tables(self, client) -> None:
        client.command(
            """
            CREATE TABLE IF NOT EXISTS agent_events (
                timestamp DateTime64(3),
                event_type String,
                source_id String,
                payer String,
                procedure String,
                payload_json String
            )
            ENGINE = MergeTree
            ORDER BY (timestamp, event_type)
            """
        )
        client.command(
            """
            CREATE TABLE IF NOT EXISTS policy_snapshots (
                timestamp DateTime64(3),
                source_id String,
                payer String,
                content_hash String,
                via String,
                normalized_content String
            )
            ENGINE = MergeTree
            ORDER BY (timestamp, source_id)
            """
        )
        client.command(
            """
            CREATE TABLE IF NOT EXISTS policy_changes (
                detected_at DateTime64(3),
                change_id String,
                watch_id String,
                policy_id String,
                payer String,
                plan_type String,
                effective_date String,
                change_type String,
                materiality String,
                summary String,
                documentation_required_json String,
                source_url String,
                diff_text String,
                payload_json String
            )
            ENGINE = MergeTree
            ORDER BY (payer, policy_id, detected_at)
            """
        )
        client.command(
            """
            CREATE TABLE IF NOT EXISTS ehr_events (
                event_time DateTime64(3),
                event_id String,
                patient_id String,
                doctor_id String,
                payer String,
                diagnosis String,
                medication_ordered String,
                procedure_code String,
                encounter_context String,
                payload_json String
            )
            ENGINE = MergeTree
            ORDER BY (payer, event_time)
            """
        )

    def integration_status(self) -> dict[str, Any]:
        has_host = bool(os.environ.get("CLICKHOUSE_HOST"))
        has_password = bool(os.environ.get("CLICKHOUSE_PASSWORD"))
        status = "connected" if self.client is not None else "connection_failed"
        details: dict[str, Any] = {
            "has_host": has_host,
            "port": os.environ.get("CLICKHOUSE_PORT", "8443"),
            "has_user": bool(os.environ.get("CLICKHOUSE_USER")),
            "has_password": has_password,
            "database": os.environ.get("CLICKHOUSE_DATABASE", "default"),
            "uses_local_jsonl_fallback": True,
        }
        if self.client is not None:
            try:
                details["ping"] = bool(self.client.ping())
            except Exception as exc:
                status = "connection_unhealthy"
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        return {
            "service": "clickhouse",
            "configured": has_host,
            "live_enabled": self.client is not None,
            "status": status,
            "failure_reason": self.last_connection_error,
            "details": details,
        }

    def log_event(self, event: AgentEvent) -> None:
        payload = event.model_dump(mode="json")
        if self.client is not None:
            try:
                self.client.insert(
                    "agent_events",
                    [
                        [
                            payload["timestamp"],
                            payload["event_type"],
                            str(payload["payload"].get("source_id", "")),
                            str(payload["payload"].get("payer", "")),
                            str(payload["payload"].get("procedure", "")),
                            json.dumps(payload["payload"]),
                        ]
                    ],
                    column_names=["timestamp", "event_type", "source_id", "payer", "procedure", "payload_json"],
                )
            except Exception as exc:
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        with self.events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    def save_snapshot(self, snapshot: PolicySnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        if self.client is not None:
            try:
                self.client.insert(
                    "policy_snapshots",
                    [
                        [
                            payload["timestamp"],
                            payload["source_id"],
                            payload["payer"],
                            payload["content_hash"],
                            payload["via"],
                            payload["normalized_content"],
                        ]
                    ],
                    column_names=[
                        "timestamp",
                        "source_id",
                        "payer",
                        "content_hash",
                        "via",
                        "normalized_content",
                    ],
                )
            except Exception as exc:
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        with self.snapshots_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    def save_policy_change(self, change: PolicyChangeRecord) -> None:
        payload = change.model_dump(mode="json")
        if self.client is not None:
            try:
                self.client.insert(
                    "policy_changes",
                    [
                        [
                            payload["detected_at"],
                            payload["change_id"],
                            payload["watch_id"],
                            payload["policy_id"],
                            payload["payer"],
                            payload["plan_type"] or "",
                            payload["effective_date"] or "",
                            payload["change_type"],
                            payload["materiality"],
                            payload["summary"],
                            json.dumps(payload["documentation_required"]),
                            payload["source_url"],
                            payload["diff_text"],
                            json.dumps(payload["payload"]),
                        ]
                    ],
                    column_names=[
                        "detected_at",
                        "change_id",
                        "watch_id",
                        "policy_id",
                        "payer",
                        "plan_type",
                        "effective_date",
                        "change_type",
                        "materiality",
                        "summary",
                        "documentation_required_json",
                        "source_url",
                        "diff_text",
                        "payload_json",
                    ],
                )
            except Exception as exc:
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        with self.policy_changes_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    def save_ehr_event(self, trigger: ClinicalTriggerEvent) -> None:
        payload = trigger.model_dump(mode="json")
        patient_context = payload.get("patient_context") or {}
        if self.client is not None:
            try:
                self.client.insert(
                    "ehr_events",
                    [
                        [
                            payload["received_at"],
                            payload["trigger_id"],
                            str(patient_context.get("patient_id", "")),
                            str(patient_context.get("doctor_id", "")),
                            payload["payer"],
                            str(patient_context.get("diagnosis", payload["procedure"])),
                            payload["procedure"],
                            (payload.get("procedure_codes") or [""])[0],
                            json.dumps(patient_context),
                            json.dumps(payload),
                        ]
                    ],
                    column_names=[
                        "event_time",
                        "event_id",
                        "patient_id",
                        "doctor_id",
                        "payer",
                        "diagnosis",
                        "medication_ordered",
                        "procedure_code",
                        "encounter_context",
                        "payload_json",
                    ],
                )
            except Exception as exc:
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        with self.ehr_events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    def get_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if self.client is not None:
            try:
                rows = self.client.query(
                    """
                    SELECT timestamp, event_type, payload_json
                    FROM agent_events
                    ORDER BY timestamp DESC
                    LIMIT %(limit)s
                    """,
                    parameters={"limit": limit},
                ).result_rows
                return [
                    {
                        "timestamp": row[0].isoformat(),
                        "event_type": row[1],
                        "payload": json.loads(row[2]),
                    }
                    for row in rows
                ]
            except Exception as exc:
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        if not self.events_file.exists():
            return []
        lines = self.events_file.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in reversed(lines)]

    def get_latest_snapshot_for_source(self, source_id: str) -> dict[str, Any] | None:
        snapshots = self.get_recent_snapshots_for_source(source_id=source_id, limit=1)
        return snapshots[0] if snapshots else None

    def get_recent_snapshots_for_source(self, source_id: str, limit: int = 2) -> list[dict[str, Any]]:
        if self.client is not None:
            try:
                rows = self.client.query(
                    """
                    SELECT timestamp, source_id, payer, content_hash, via, normalized_content
                    FROM policy_snapshots
                    WHERE source_id = %(source_id)s
                    ORDER BY timestamp DESC
                    LIMIT %(limit)s
                    """,
                    parameters={"source_id": source_id, "limit": limit},
                ).result_rows
                return [
                    {
                        "timestamp": row[0].isoformat(),
                        "source_id": row[1],
                        "payer": row[2],
                        "content_hash": row[3],
                        "via": row[4],
                        "normalized_content": row[5],
                    }
                    for row in rows
                ]
            except Exception as exc:
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        if not self.snapshots_file.exists():
            return []
        lines = self.snapshots_file.read_text(encoding="utf-8").splitlines()
        matches: list[dict[str, Any]] = []
        for line in reversed(lines):
            payload = json.loads(line)
            if payload["source_id"] == source_id:
                matches.append(payload)
                if len(matches) >= limit:
                    break
        return matches

    def get_recent_policy_changes(self, limit: int = 25) -> list[dict[str, Any]]:
        if self.client is not None:
            try:
                rows = self.client.query(
                    """
                    SELECT detected_at, payload_json
                    FROM policy_changes
                    ORDER BY detected_at DESC
                    LIMIT %(limit)s
                    """,
                    parameters={"limit": limit},
                ).result_rows
                return [
                    {
                        "detected_at": row[0].isoformat(),
                        **json.loads(row[1]),
                    }
                    for row in rows
                ]
            except Exception as exc:
                self.last_connection_error = f"{type(exc).__name__}: {exc}"
        if not self.policy_changes_file.exists():
            return []
        lines = self.policy_changes_file.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in reversed(lines)]

    def get_kpis(self) -> dict[str, int]:
        events = self.get_recent_events(limit=1000)
        counts: dict[str, int] = {}
        for event in events:
            event_type = event["event_type"]
            counts[event_type] = counts.get(event_type, 0) + 1
        policy_changes = self.get_recent_policy_changes(limit=500)
        material_count = sum(1 for change in policy_changes if change.get("materiality") == "material")
        ambiguous_count = sum(1 for change in policy_changes if change.get("materiality") == "ambiguous")
        non_material_count = sum(1 for change in policy_changes if change.get("materiality") == "non_material")
        return {
            "total_events": len(events),
            "material_changes": material_count,
            "non_material_changes": non_material_count,
            "ambiguous_changes": ambiguous_count,
            "cds_hooks_received": counts.get("cds_hook_received", 0),
            "crd_ready": counts.get("crd_guidance_ready", 0),
            "dtr_requests": counts.get("dtr_requested", 0),
            "payments_verified": counts.get("payment_verified", 0),
            "readiness_cards_generated": counts.get("readiness_card_generated", 0),
        }
