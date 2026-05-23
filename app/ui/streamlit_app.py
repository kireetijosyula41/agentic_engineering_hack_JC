from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st


API_BASE = os.environ.get("POLICYPULSE_API_BASE", "http://localhost:8000")
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


def post_json(path: str, payload: dict | None = None):
    response = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=60)
    if response.status_code >= 400:
        try:
            return {"error": response.json()}
        except Exception:
            return {"error": response.text}
    return response.json()


def get_json(path: str):
    response = requests.get(f"{API_BASE}{path}", timeout=60)
    response.raise_for_status()
    return response.json()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def load_trusted_sources() -> list[dict]:
    return json.loads((FIXTURES_DIR / "trusted_sources.json").read_text(encoding="utf-8"))


def render_card(title: str, value: str, subtitle: str = "", accent: str = "#0f766e") -> None:
    st.markdown(
        f"""
        <div style="border:1px solid {accent}33; border-radius:16px; padding:16px; background:linear-gradient(135deg, {accent}10, #ffffff); min-height:120px;">
          <div style="font-size:12px; letter-spacing:0.08em; text-transform:uppercase; color:{accent}; font-weight:700;">{title}</div>
          <div style="font-size:28px; font-weight:800; margin-top:10px; color:#111827;">{value}</div>
          <div style="font-size:14px; color:#4b5563; margin-top:8px;">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_badge(text: str, color: str) -> str:
    return (
        f"<span style='display:inline-block; padding:6px 10px; border-radius:999px; "
        f"background:{color}22; color:{color}; font-weight:700; font-size:12px; margin-right:8px;'>{text}</span>"
    )


def extract_policy_monitor(source_id: str | None = None) -> dict:
    try:
        suffix = f"?source_id={source_id}" if source_id else ""
        return get_json(f"/api/policy-monitor{suffix}")
    except Exception:
        return {"policy_change": None, "current_snapshot": None, "previous_snapshot": None}


def extract_monitor_status() -> dict:
    try:
        return get_json("/api/monitor-status")
    except Exception:
        return {}


def infer_monitor_status(monitor_status: dict, monitor: dict, scan_result: dict) -> dict:
    if monitor_status:
        return monitor_status

    policy_change = (monitor or {}).get("policy_change") or {}
    change_payload = policy_change.get("payload", policy_change) if policy_change else {}
    current_snapshot = (monitor or {}).get("current_snapshot") or {}
    active_changes = (scan_result or {}).get("active_policy_changes", [])

    source_ids: list[str] = []
    for item in active_changes:
        source_id = item.get("source_id")
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
    if not source_ids:
        source_id = change_payload.get("source_id") or current_snapshot.get("source_id")
        if source_id:
            source_ids.append(source_id)

    inferred_result = "changes_detected" if active_changes or policy_change else "scan_pending"
    inferred_started = st.session_state.get("scan_started_at")
    inferred_completed = st.session_state.get("scan_completed_at")

    return {
        "mode": "automatic_on_load_plus_manual_rescan",
        "monitor_running": bool(active_changes or policy_change or inferred_started),
        "sources_monitored": len(source_ids),
        "source_ids": source_ids,
        "last_scan_started_at": inferred_started,
        "last_scan_completed_at": inferred_completed,
        "last_result": inferred_result,
        "latest_materiality": (
            change_payload.get("materiality", {}).get("classification")
            if isinstance(change_payload.get("materiality"), dict)
            else change_payload.get("materiality")
        ),
        "latest_change_summary": change_payload.get("diff", {}).get("summary") or change_payload.get("summary"),
        "status_source": "ui_fallback",
    }


def bootstrap_autonomous_monitor(source_id: str | None = None) -> dict:
    suffix = f"?source_id={source_id}" if source_id else ""
    start_result = post_json(f"/api/monitor/start{suffix}")
    if isinstance(start_result, dict) and start_result.get("error"):
        fallback_scan = post_json(f"/api/watch/scan{suffix}")
        status = infer_monitor_status({}, {}, fallback_scan if isinstance(fallback_scan, dict) else {})
        status["status_source"] = "ui_bootstrap_fallback"
        return status

    last_status = start_result if isinstance(start_result, dict) else {}
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            last_status = get_json("/api/monitor-status")
        except Exception:
            time.sleep(1)
            continue
        if last_status.get("last_scan_completed_at") or last_status.get("last_result") in {
            "changes_detected",
            "no_changes_detected",
            "scan_failed",
        }:
            return last_status
        time.sleep(1)
    return last_status if isinstance(last_status, dict) else {}


def extract_hook_result() -> dict:
    value = st.session_state.get("hook_result", {})
    return value if isinstance(value, dict) else {}


def extract_readiness() -> dict:
    value = st.session_state.get("readiness_result", {})
    return value if isinstance(value, dict) else {}


def extract_payment() -> dict:
    value = st.session_state.get("payment_required", {})
    return value if isinstance(value, dict) else {}


def first_lines(text: str | None, limit: int = 12) -> str:
    if not text:
        return "No content available yet."
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:limit])


def format_diff_lines(change: dict) -> str:
    payload = change.get("payload", change) if change else {}
    diff = payload.get("diff", {})
    changed_lines = diff.get("changed_lines", [])
    if not changed_lines:
        diff_text = change.get("diff_text", "")
        return diff_text or "No diff available yet."
    return "\n".join(changed_lines[:20])


def materiality_details(policy_change: dict) -> tuple[str, dict, dict]:
    change_payload = policy_change.get("payload", policy_change) if policy_change else {}
    materiality_value = change_payload.get("materiality", {})
    if isinstance(materiality_value, dict):
        materiality_label = materiality_value.get("classification", "unknown")
    else:
        materiality_label = str(materiality_value or "unknown")
    return materiality_label, materiality_value if isinstance(materiality_value, dict) else {}, change_payload


def display_doctor_action_panel() -> None:
    st.markdown("### 3. Doctor Upload / EHR Trigger")
    st.caption("A judge can either use the demo buttons or upload a JSON payload that looks like a CDS Hooks doctor action.")

    button_col1, button_col2 = st.columns(2)
    with button_col1:
        if st.button("Use Relevant Doctor Fixture", use_container_width=True):
            st.session_state["hook_result"] = post_json("/api/cds-hooks", load_fixture("cds_order_sign_matching.json"))
            st.session_state.pop("payment_required", None)
            st.session_state.pop("readiness_result", None)
    with button_col2:
        if st.button("Use Irrelevant Doctor Fixture", use_container_width=True):
            st.session_state["hook_result"] = post_json("/api/cds-hooks", load_fixture("cds_order_sign_nonmatching.json"))
            st.session_state.pop("payment_required", None)
            st.session_state.pop("readiness_result", None)

    uploaded = st.file_uploader("Upload doctor EHR event JSON", type=["json"])
    if uploaded is not None:
        try:
            payload = json.loads(uploaded.getvalue().decode("utf-8"))
            st.code(json.dumps(payload, indent=2), language="json")
            if st.button("Submit Uploaded Doctor Event", use_container_width=True):
                st.session_state["hook_result"] = post_json("/api/cds-hooks", payload)
                st.session_state.pop("payment_required", None)
                st.session_state.pop("readiness_result", None)
        except Exception as exc:
            st.error(f"Could not parse uploaded JSON: {exc}")

    hook_result = extract_hook_result()
    crd = hook_result.get("crd_decision", {})
    dtr = hook_result.get("dtr_request", {})
    route = crd.get("route", "not_run")
    if route == "crd_guidance_ready":
        status_title = "Relevant To Caught Diff"
        accent = "#166534"
        subtitle = crd.get("reason", "This doctor action matches the monitored policy change.")
    elif route == "suppress":
        status_title = "Not Relevant To Caught Diff"
        accent = "#b45309"
        subtitle = crd.get("reason", "This doctor action does not match the monitored policy change.")
    elif route == "human_review":
        status_title = "Human Review Needed"
        accent = "#7c3aed"
        subtitle = crd.get("reason", "The relationship to the policy change is ambiguous.")
    else:
        status_title = "Awaiting Doctor Event"
        accent = "#6b7280"
        subtitle = "Upload or simulate a doctor action to evaluate CRD/DTR relevance."

    st.markdown("#### CRD / DTR Relevance")
    rel_left, rel_right = st.columns([0.8, 1.2])
    with rel_left:
        render_card("Relevance Status", status_title, subtitle, accent)
    with rel_right:
        trigger = hook_result.get("clinical_trigger", {})
        lines = [
            f"**Payer:** {trigger.get('payer', 'Not provided')}",
            f"**Plan:** {trigger.get('plan', 'Not provided')}",
            f"**Procedure:** {trigger.get('procedure', 'Not provided')}",
            f"**Provider action:** {trigger.get('provider_action', 'Not provided')}",
            f"**CRD route:** {route.replace('_', ' ').title() if route else 'Not evaluated'}",
            f"**DTR evidence needed:** {', '.join(dtr.get('required_evidence', [])) if dtr.get('required_evidence') else 'None yet'}",
        ]
        st.markdown(
            f"""
            <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px; background:#ffffff;">
              {'<br>'.join(lines)}
            </div>
            """,
            unsafe_allow_html=True,
        )


st.set_page_config(page_title="PolicyPulse MVP", layout="wide")
st.title("PolicyPulse MVP")
st.caption(
    "Autonomous UHC policy monitoring for the hackathon: Nimble fetches public policy pages, "
    "ClickHouse stores the changing policy memory, CRD/DTR decide relevance, and Senso returns and publishes downstream guidance."
)

trusted_sources = load_trusted_sources()
default_monitored_source_id = trusted_sources[0]["source_id"] if trusted_sources else None
if "monitored_source_id" not in st.session_state:
    st.session_state["monitored_source_id"] = default_monitored_source_id

if "monitor_bootstrapped" not in st.session_state:
    with st.spinner("Nimble is scanning UHC policy pages and comparing them with ClickHouse snapshots..."):
        st.session_state["monitor_bootstrap_status"] = bootstrap_autonomous_monitor(st.session_state["monitored_source_id"])
        st.session_state["scan_started_at"] = st.session_state["monitor_bootstrap_status"].get("last_scan_started_at")
        st.session_state["scan_completed_at"] = st.session_state["monitor_bootstrap_status"].get("last_scan_completed_at")
        st.session_state["monitor_bootstrapped"] = True
    st.session_state.pop("hook_result", None)
    st.session_state.pop("payment_required", None)
    st.session_state.pop("readiness_result", None)

top_left, top_mid, top_right = st.columns(3)
with top_left:
    if st.button("1. Run Policy Check", use_container_width=True):
        st.session_state["scan_started_at"] = datetime.now().isoformat(timespec="seconds")
        source_id = st.session_state.get("monitored_source_id")
        suffix = f"?source_id={source_id}" if source_id else ""
        st.session_state["scan_result"] = post_json(f"/api/watch/scan{suffix}")
        st.session_state["scan_completed_at"] = datetime.now().isoformat(timespec="seconds")
        st.session_state.pop("hook_result", None)
        st.session_state.pop("payment_required", None)
        st.session_state.pop("readiness_result", None)
with top_mid:
    st.empty()
with top_right:
    st.empty()

monitor = extract_policy_monitor(st.session_state.get("monitored_source_id"))
monitor_status = infer_monitor_status(
    extract_monitor_status() or st.session_state.get("monitor_bootstrap_status", {}),
    monitor,
    st.session_state.get("scan_result", {}),
)
policy_change = monitor.get("policy_change") or {}
materiality_label, materiality_value, change_payload = materiality_details(policy_change)

hook_result = extract_hook_result()
crd = hook_result.get("crd_decision", {})
payment = extract_payment()
readiness = extract_readiness()
publication = readiness.get("publication", {}) if isinstance(readiness, dict) else {}

st.markdown("## Judge Summary")
sum1, sum2, sum3, sum4, sum5 = st.columns(5)
with sum1:
    render_card("Policy Changed", "Yes" if policy_change else "No", change_payload.get("summary", "Run the policy monitor to populate this."), "#0f766e")
with sum2:
    render_card("Classification", materiality_label.replace("_", " ").title(), change_payload.get("change_type", "No change type yet."), "#7c3aed")
with sum3:
    route = crd.get("route", "not_run")
    render_card("CRD / DTR Relevance", "Relevant" if route == "crd_guidance_ready" else "No Alert" if route == "suppress" else route, crd.get("reason", "Upload or simulate a doctor action."), "#1d4ed8")
with sum4:
    payment_state = "Verified" if readiness.get("payment", {}).get("verified") else "Required" if payment else "Not Started"
    render_card("Payment", payment_state, readiness.get("payment", {}).get("message", payment.get("error", {}).get("detail", {}).get("message", "Premium step not started.")) if isinstance(payment.get("error", {}), dict) else "Premium step not started.", "#b45309")
with sum5:
    render_card("Senso Publish", "Published" if publication.get("success") else "Pending", publication.get("url") or publication.get("message", "No cited.md record yet."), "#be123c")

st.markdown("## 1. Policy Diff From Nimble vs Current Policy In ClickHouse")
@st.fragment(run_every="15s")
def render_monitor_fragment() -> None:
    current_monitor = extract_policy_monitor(st.session_state.get("monitored_source_id"))
    current_status = infer_monitor_status(
        extract_monitor_status() or st.session_state.get("monitor_bootstrap_status", {}),
        current_monitor,
        st.session_state.get("scan_result", {}),
    )
    current_policy_change = current_monitor.get("policy_change") or {}
    current_materiality_label, current_materiality_value, current_change_payload = materiality_details(current_policy_change)
    left, right = st.columns([1.1, 1])
    with left:
        if current_status.get("scan_in_progress") and not current_policy_change:
            st.info("Nimble is currently checking the selected UHC source against the current ClickHouse snapshot. This panel will populate as soon as the first scan completes.")
        elif not current_policy_change:
            st.info("The autonomous monitor is running against the selected source URL. If a new diff is detected, it will appear here after the current scan completes.")
        else:
            badges = []
            badges.append(render_badge(current_change_payload.get("payer", "Unknown payer"), "#0f766e"))
            badges.append(render_badge(current_change_payload.get("plan_type", "Unknown plan"), "#1d4ed8"))
            badges.append(render_badge(current_materiality_label.replace("_", " ").title(), "#7c3aed"))
            st.markdown("".join(badges), unsafe_allow_html=True)
            st.markdown(f"**Source URL:** {current_change_payload.get('source_url') or current_monitor.get('source_url', 'Run the monitor first.')}")
            st.markdown(f"**Changed section:** {current_change_payload.get('changed_section', 'Unknown')}")
            st.markdown(f"**Effective date:** {current_change_payload.get('effective_date', 'Unknown')}")
            st.markdown("#### Diff received from Nimble")
            st.code(format_diff_lines(current_policy_change), language="diff")
    with right:
        current_snapshot = current_monitor.get("current_snapshot") or {}
        if current_snapshot:
            st.markdown("#### Current policy stored in ClickHouse")
            st.markdown(
                f"""
                <div style="border:1px solid #d1d5db; border-radius:16px; padding:16px; background:#f8fafc;">
                  <div style="font-size:12px; color:#475569; margin-bottom:8px;"><strong>Snapshot hash:</strong> {current_snapshot.get('content_hash', 'Unavailable')}</div>
                  <div style="font-size:12px; color:#475569; margin-bottom:12px;"><strong>Stored via:</strong> {current_snapshot.get('via', 'Unavailable')}</div>
                  <pre style="white-space:pre-wrap; font-size:12px; line-height:1.45; color:#0f172a;">{first_lines(current_snapshot.get('normalized_content'))}</pre>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.info("The latest ClickHouse snapshot for the selected source will appear here after the policy check runs.")
        st.markdown("#### Autonomous Monitor Status")
        mode = current_status.get("mode", "unknown").replace("_", " ").title()
        running = "Running" if current_status.get("monitor_running") else "Idle"
        last_result = current_status.get("last_result", "unknown").replace("_", " ").title()
        latest_materiality = (current_status.get("latest_materiality") or "unknown").replace("_", " ").title()
        monitored_sources = ", ".join(current_status.get("monitored_source_ids", current_status.get("source_ids", []))) or "No sources configured"
        st.markdown(
            f"""
            <div style="border:1px solid #bfdbfe; border-radius:16px; padding:16px; background:linear-gradient(135deg, #dbeafe55, #ffffff); margin-top:14px;">
              <div style="font-size:12px; letter-spacing:0.08em; text-transform:uppercase; color:#1d4ed8; font-weight:800; margin-bottom:10px;">Autonomous Monitor Status</div>
              <div style="margin-bottom:8px;"><strong>Mode:</strong> {mode}</div>
              <div style="margin-bottom:8px;"><strong>Status:</strong> {running}</div>
              <div style="margin-bottom:8px;"><strong>Sources monitored:</strong> {current_status.get("sources_monitored", 0)}</div>
              <div style="margin-bottom:8px;"><strong>Source IDs:</strong> {monitored_sources}</div>
              <div style="margin-bottom:8px;"><strong>Last scan started:</strong> {current_status.get("last_scan_started_at", "Unknown")}</div>
              <div style="margin-bottom:8px;"><strong>Last scan completed:</strong> {current_status.get("last_scan_completed_at", "Unknown")}</div>
              <div style="margin-bottom:8px;"><strong>Last result:</strong> {last_result}</div>
              <div style="margin-bottom:0;"><strong>Latest materiality:</strong> {latest_materiality}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if current_status.get("status_source") == "ui_fallback":
            st.caption("Showing inferred monitor status from the latest UI-triggered scan because the backend monitor-status route is not reachable from this session.")
        elif current_status.get("status_source") == "ui_bootstrap_fallback":
            st.caption("The autonomous monitor start route was not reachable, so the UI used a one-time fallback scan for this session.")
        else:
            st.caption(f"The backend monitor is rescanning the selected source every {current_status.get('scan_interval_seconds', 15)} seconds.")

    st.markdown("## 2. Classification")
    classification_col1, classification_col2 = st.columns([0.7, 1.3])
    with classification_col1:
        color = {"material": "#166534", "non_material": "#92400e", "ambiguous": "#7c3aed"}.get(current_materiality_label, "#6b7280")
        render_card(
            "Diff Classification",
            current_materiality_label.replace("_", " ").title() if current_policy_change else "Not Run",
            current_change_payload.get("diff", {}).get("summary", "Run the policy check to classify the diff."),
            color,
        )
    with classification_col2:
        st.markdown("#### Why the agent classified it this way")
        explanation = current_materiality_value.get("explanation", "Run the policy check to generate a classification.")
        impacted = current_materiality_value.get("impacted_keywords", [])
        st.markdown(
            f"""
            <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px; background:#ffffff;">
              <div style="margin-bottom:12px;">{explanation}</div>
              <div><strong>Impacted signals:</strong> {', '.join(impacted) if impacted else 'None detected'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


render_monitor_fragment()

display_doctor_action_panel()

st.markdown("## 4. Payment Verification")
payment_col1, payment_col2 = st.columns([0.8, 1.2])
with payment_col1:
    if crd.get("route") == "crd_guidance_ready":
        if st.button("Run Payment Verification + Senso", use_container_width=True, type="primary"):
            readiness_request = {
                "clinical_trigger": hook_result["clinical_trigger"],
                "crd_decision": hook_result["crd_decision"],
            }
            st.session_state["payment_required"] = post_json("/api/readiness-card", readiness_request)
            st.session_state["readiness_result"] = post_json(
                "/api/readiness-card",
                {
                    **readiness_request,
                    "payment_token": "demo-paid",
                },
            )
    else:
        st.button("Run Payment Verification + Senso", use_container_width=True, disabled=True)

    if readiness.get("payment", {}).get("verified"):
        render_card("Payment Status", "Verified", readiness["payment"].get("transaction_id", "demo-paid"), "#166534")
    elif payment:
        detail = payment.get("error", {}).get("detail", {}) if isinstance(payment.get("error"), dict) else {}
        render_card("Payment Status", "Required", detail.get("message", "This premium step is gated."), "#b45309")
    else:
        render_card("Payment Status", "Not Started", "Generate the payment prompt or verify payment to continue.", "#6b7280")
with payment_col2:
    st.markdown("#### Relevant doctor trigger")
    trigger = hook_result.get("clinical_trigger", {})
    dtr = hook_result.get("dtr_request", {})
    lines = [
        f"**Payer:** {trigger.get('payer', 'Not provided')}",
        f"**Plan:** {trigger.get('plan', 'Not provided')}",
        f"**Procedure:** {trigger.get('procedure', 'Not provided')}",
        f"**Provider action:** {trigger.get('provider_action', 'Not provided')}",
        f"**CRD route:** {crd.get('route', 'Not evaluated')}",
        f"**DTR evidence needed:** {', '.join(dtr.get('required_evidence', [])) if dtr.get('required_evidence') else 'None yet'}",
    ]
    if crd.get("route") != "crd_guidance_ready":
        lines.append("**Next step:** This action is not relevant to the caught diff, so payment and Senso stay disabled.")
    st.markdown(
        f"""
        <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px; background:#ffffff;">
          {'<br>'.join(lines)}
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("## 5. Senso Returned Object")
senso_col1, senso_col2 = st.columns([1, 1])
with senso_col1:
    checklist = readiness.get("checklist", {})
    st.markdown(
        f"""
        <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px; background:#ffffff;">
          <div style="font-weight:800; margin-bottom:8px;">Checklist source</div>
          <div style="margin-bottom:12px;">{checklist.get('source', 'Not run yet')}</div>
          <div style="font-weight:800; margin-bottom:8px;">Returned items</div>
          <ul>
            {''.join(f'<li>{item}</li>' for item in checklist.get('items', [])) or '<li>No checklist returned yet.</li>'}
          </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
with senso_col2:
    st.markdown(
        f"""
        <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px; background:#ffffff;">
          <div style="font-weight:800; margin-bottom:8px;">Publication to cited.md</div>
          <div><strong>Status:</strong> {'Success' if publication.get('success') else 'Not published yet'}</div>
          <div><strong>Publisher:</strong> {publication.get('publisher_slug', 'cited-md')}</div>
          <div><strong>Question ID:</strong> {publication.get('question_id', 'N/A')}</div>
          <div><strong>Content ID:</strong> {publication.get('content_id', 'N/A')}</div>
          <div><strong>URL:</strong> {publication.get('url', publication.get('message', 'No publication result yet.'))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with st.expander("Technical Trace", expanded=False):
    trace_col1, trace_col2 = st.columns(2)
    with trace_col1:
        st.caption("Latest policy change payload")
        st.json(policy_change)
    with trace_col2:
        st.caption("Latest readiness payload")
        st.json(readiness)
