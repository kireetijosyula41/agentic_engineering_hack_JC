from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.models import CRDDecision, ClinicalTriggerEvent
from app.runtime import service


app = FastAPI(title="PolicyPulse MVP", version="0.1.0")


class CRDEvaluateRequest(BaseModel):
    clinical_trigger: ClinicalTriggerEvent


class DTREvaluateRequest(BaseModel):
    clinical_trigger: ClinicalTriggerEvent
    crd_decision: CRDDecision


class ReadinessCardRequest(BaseModel):
    clinical_trigger: dict
    crd_decision: dict
    payment_token: str | None = None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/integrations/status")
def integration_status() -> dict:
    return service.get_integration_status()


@app.post("/api/watch/scan")
def watch_scan(source_id: str | None = Query(default=None)) -> dict:
    changes = service.scan_sources(scan_origin="manual", source_ids=[source_id] if source_id else None)
    return {"active_policy_changes": [item.model_dump(mode="json") for item in changes]}


@app.post("/api/monitor/start")
def monitor_start(source_id: str | None = Query(default=None)) -> dict:
    return service.start_autonomous_monitor(source_id=source_id)


@app.get("/api/active-policy-changes")
def active_policy_changes() -> dict:
    items = service.get_active_policy_changes()
    return {"active_policy_changes": [item.model_dump(mode="json") for item in items]}


@app.get("/api/policy-changes")
def policy_changes() -> dict:
    return {"policy_changes": service.get_recent_policy_changes()}


@app.get("/api/policy-monitor")
def policy_monitor(source_id: str | None = Query(default=None)) -> dict:
    return service.get_policy_monitor_view(source_id=source_id)


@app.get("/api/monitor-status")
def monitor_status() -> dict:
    return service.get_monitor_status()


@app.post("/api/cds-hooks")
def cds_hooks(payload: dict) -> dict:
    trigger = service.ingest_cds_hook(payload)
    decision = service.evaluate_crd(trigger)
    response = {
        "clinical_trigger": trigger.model_dump(mode="json"),
        "crd_decision": decision.model_dump(mode="json"),
    }
    if decision.route == "crd_guidance_ready":
        dtr_request = service.evaluate_dtr(trigger, decision)
        response["dtr_request"] = dtr_request.model_dump(mode="json")
    return response


@app.post("/api/crd/evaluate")
def crd_evaluate(request: CRDEvaluateRequest) -> dict:
    decision = service.evaluate_crd(request.clinical_trigger)
    return decision.model_dump(mode="json")


@app.post("/api/dtr/evaluate")
def dtr_evaluate(request: DTREvaluateRequest) -> dict:
    if request.crd_decision.route != "crd_guidance_ready":
        raise HTTPException(status_code=400, detail="DTR evaluation requires a CRD-guidance-ready decision.")
    dtr_request = service.evaluate_dtr(request.clinical_trigger, request.crd_decision)
    return dtr_request.model_dump(mode="json")


@app.post("/api/readiness-card")
def readiness_card(request: ReadinessCardRequest) -> dict:
    if not request.payment_token:
        requirement = service.require_payment(request.model_dump(mode="json"))
        raise HTTPException(status_code=402, detail=requirement)
    try:
        result = service.generate_readiness_card(request.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result["status"] != "ok":
        raise HTTPException(status_code=402, detail=result["payment"])
    return result


@app.get("/api/events")
def events() -> dict:
    return {"events": service.store.get_recent_events()}


@app.get("/api/kpis")
def kpis() -> dict:
    return service.store.get_kpis()
