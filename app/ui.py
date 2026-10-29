"""Streamlit interview dashboard for the telemetry FastAPI service.

Run with ``streamlit run app/ui.py`` and set API_BASE_URL when the API is remote.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import os
from statistics import mean
from typing import Any
from uuid import uuid4

import httpx
import plotly.graph_objects as go
import streamlit as st


API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 90.0

PAIN_EXAMPLES = [
    "How might walking-related pelvic loading contribute to pain-associated cardiovascular burden?",
    "How could inline skating reduce mechanical pain while maintaining cardiovascular exercise?",
    "How might ParaTransit vehicle vibration and jolts provoke pelvic pain and autonomic stress?",
]


def _url(path: str) -> str:
    return f"{API_BASE_URL}{path}"


def _api_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.text.strip()
        return f"HTTP {exc.response.status_code}: {detail[:500]}"
    return str(exc)


@st.cache_data(ttl=30, show_spinner=False)
def _get(path: str) -> dict[str, Any]:
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.get(_url(path))
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{path} returned an unexpected response")
    return payload


def _post(path: str, **kwargs: Any) -> dict[str, Any]:
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(_url(path), **kwargs)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{path} returned an unexpected response")
    return payload


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _sources(record: dict[str, Any]) -> list[str]:
    values: list[Any] = [record.get("source")]
    values.extend(record.get("sources") or [])
    values.extend((record.get("original_sources") or {}).values())
    return [str(value) for value in values if value]


def _unique_workouts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match the analysis API's WHOOP identity and first-candidate policy."""
    seen: set[tuple[Any, Any, float | None]] = set()
    unique = []
    for record in records:
        original_times = record.get("original_timestamps") or {}
        original_activities = record.get("original_activities") or {}
        identity = (
            original_times.get("whoop_start") or record.get("timestamp"),
            original_activities.get("whoop") or record.get("activity"),
            _number(record.get("duration_minutes")),
        )
        if identity not in seen:
            seen.add(identity)
            unique.append(record)
    return unique


def _activity(record: dict[str, Any]) -> str:
    return str(record.get("activity") or "Unknown")


def _overview(records: list[dict[str, Any]], workouts: list[dict[str, Any]]) -> None:
    mean_hr = [value for row in workouts if (value := _number((row.get("heart_rate") or {}).get("mean_bpm"))) is not None]
    max_hr = [value for row in workouts if (value := _number((row.get("heart_rate") or {}).get("max_bpm"))) is not None]
    hrv_count = sum(_number((row.get("hrv") or {}).get("hrv_ms")) is not None for row in workouts)
    devices = sorted({source.upper() if source.lower() in {"whoop", "polar"} else "Strava"
                      for row in records for source in _sources(row)
                      if source.lower() in {"whoop", "strava", "polar"}})
    cards = st.columns(4)
    cards[0].metric("Rows returned", len(records))
    cards[1].metric("Unique WHOOP workouts", len(workouts))
    cards[2].metric("Devices observed", len(devices), help=", ".join(devices) or "No device source available")
    cards[3].metric("Activity categories", len({_activity(row) for row in workouts}))
    cards = st.columns(3)
    cards[0].metric("Average workout mean HR", f"{mean(mean_hr):.1f} bpm" if mean_hr else "N/A")
    cards[1].metric("Highest workout max HR", f"{max(max_hr):.0f} bpm" if max_hr else "N/A")
    cards[2].metric("Daily recovery HRV available", f"{hrv_count}/{len(workouts)} workouts")
    st.caption("HR and HRV cards use unique workouts with available measurements. HRV is WHOOP daily recovery context, not activity HRV.")


def _coverage_warnings(records: list[dict[str, Any]], total: int) -> None:
    if total > len(records):
        st.warning("Only part of the telemetry collection was returned. Coverage checks use the displayed records.")
    if not any("walk" in str(record.get("activity") or "").lower() for record in records):
        st.warning("No walking observations appear in the returned telemetry. Walking comparisons are unavailable.")
    if not any("polar" in source.lower() for record in records for source in _sources(record)):
        st.warning("No Polar observations appear in the returned telemetry. Device comparisons are unavailable.")
    st.info("HRV values, when present, are WHOOP daily recovery context; they are not activity HRV measurements.")


def _chart_activity(workouts: list[dict[str, Any]]) -> None:
    counts = Counter(_activity(record) for record in workouts)
    if not counts:
        return
    labels, values = zip(*counts.most_common())
    figure = go.Figure(go.Bar(x=list(labels), y=list(values), marker_color="#2b7a78"))
    figure.update_layout(
        title="Unique WHOOP workouts by activity",
        xaxis_title="Activity",
        yaxis_title="Workouts",
        yaxis=dict(dtick=1),
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(figure, width="stretch")


def _chart_heart_rate(workouts: list[dict[str, Any]]) -> None:
    points: list[tuple[datetime, float, str, str]] = []
    for record in workouts:
        heart_rate = record.get("heart_rate") or {}
        when = _timestamp(record.get("timestamp"))
        bpm = _number(heart_rate.get("mean_bpm"))
        if when is not None and bpm is not None:
            points.append((when, bpm, str(record.get("activity") or "Unknown"), str(record.get("record_id") or "")))
    if not points:
        st.caption("Mean heart rate chart unavailable: no records have both a valid timestamp and mean heart rate.")
        return
    points.sort(key=lambda item: item[0])
    figure = go.Figure(
        go.Scatter(
            x=[point[0] for point in points],
            y=[point[1] for point in points],
            mode="markers",
            marker=dict(size=9, color="#e58f47"),
            customdata=[[point[2], point[3]] for point in points],
            hovertemplate="%{x}<br>%{y:.1f} bpm<br>%{customdata[0]}<br>Record: %{customdata[1]}<extra></extra>",
        )
    )
    figure.update_layout(
        title="Mean heart rate by recorded time",
        xaxis_title="Recorded time",
        yaxis_title="Mean heart rate (bpm)",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(figure, width="stretch")


def _chart_group_metric(
    workouts: list[dict[str, Any]],
    title: str,
    field: str,
    unit: str,
    color: str,
    aggregate: str = "mean",
) -> None:
    grouped: dict[str, list[float]] = {}
    for row in workouts:
        if field == "duration_minutes":
            value = _number(row.get(field))
        else:
            parent, child = field.split(".")
            value = _number((row.get(parent) or {}).get(child))
        if value is not None:
            grouped.setdefault(_activity(row), []).append(value)
    if not grouped:
        st.caption(f"{title} unavailable: no unique workouts have this measurement.")
        return
    labels = sorted(grouped)
    figure = go.Figure(
        go.Bar(
            x=labels,
            y=[max(grouped[label]) if aggregate == "max" else mean(grouped[label]) for label in labels],
            marker_color=color,
            customdata=[len(grouped[label]) for label in labels],
            hovertemplate="%{x}<br>%{y:.1f} " + unit + "<br>%{customdata} workouts with data<extra></extra>",
        )
    )
    figure.update_layout(
        title=title,
        xaxis_title="Activity",
        yaxis_title=unit,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(figure, width="stretch")


def _chart_burden(analysis: dict[str, Any] | None) -> None:
    activities = (analysis or {}).get("activities")
    if not isinstance(activities, dict):
        st.caption("Comparative burden unavailable: the analysis API returned no activity summary.")
        return
    plotted = []
    for activity, values in activities.items():
        if isinstance(values, dict) and (burden := _number(values.get("mean_burden_proxy"))) is not None:
            plotted.append((str(activity), burden, values.get("burden_sample_count")))
    if not plotted:
        st.caption("Comparative burden unavailable: no activities have both duration and mean heart rate.")
        return
    plotted.sort(key=lambda item: item[0])
    figure = go.Figure(
        go.Bar(
            x=[item[0] for item in plotted],
            y=[item[1] for item in plotted],
            customdata=[item[2] for item in plotted],
            marker_color="#8d68a8",
            hovertemplate="%{x}<br>%{y:.1f} proxy units<br>%{customdata} workouts with data<extra></extra>",
        )
    )
    figure.update_layout(
        title="Comparative burden by activity (API analysis)",
        xaxis_title="Activity",
        yaxis_title="Mean burden proxy",
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(figure, width="stretch")
    st.caption(str((analysis or {}).get("burden_method") or "Descriptive burden proxy from API analysis; not a clinical load measure."))


def _telemetry_panel() -> None:
    st.subheader("Telemetry")
    try:
        payload = _get("/telemetry")
        records = payload["records"]
        total = payload["total"]
        if not isinstance(records, list) or not isinstance(total, int):
            raise ValueError("/telemetry returned invalid records or total")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        st.error(f"Telemetry unavailable: {_api_error(exc)}")
        return

    _coverage_warnings(records, total)
    if not records:
        st.metric("Rows reported by API", total)
        st.info("No telemetry records are available yet. Upload a supported file to ingest data.")
        return

    workouts = _unique_workouts(records)
    _overview(records, workouts)
    if len(workouts) < len(records):
        st.caption(f"Charts use {len(workouts)} unique WHOOP workouts from {len(records)} candidate rows; repeated join rows are excluded.")
    try:
        analysis = _get("/analysis").get("results")
        if not isinstance(analysis, dict):
            analysis = None
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        analysis = None
        st.caption(f"Analysis API unavailable for burden chart: {_api_error(exc)}")

    left, right = st.columns(2)
    with left:
        _chart_activity(workouts)
    with right:
        _chart_group_metric(workouts, "Mean heart rate by activity", "heart_rate.mean_bpm", "Mean HR (bpm)", "#e58f47")
    left, right = st.columns(2)
    with left:
        _chart_group_metric(workouts, "Max heart rate by activity", "heart_rate.max_bpm", "Highest workout max HR (bpm)", "#c85c5c", aggregate="max")
    with right:
        _chart_group_metric(workouts, "Activity duration", "duration_minutes", "Mean duration (minutes)", "#4477aa")
    left, right = st.columns(2)
    with left:
        _chart_group_metric(workouts, "WHOOP daily recovery HRV by activity", "hrv.hrv_ms", "Mean daily recovery HRV (ms)", "#55a38a")
        st.caption("HRV is WHOOP daily recovery context associated with these workouts; it was not measured during the activities.")
    with right:
        _chart_burden(analysis)
    with st.expander("Mean heart rate over recorded time"):
        _chart_heart_rate(workouts)

    with st.expander("Source records"):
        st.dataframe(
            [
                {
                    "record_id": record.get("record_id"),
                    "timestamp": record.get("timestamp"),
                    "activity": record.get("activity"),
                    "sources": ", ".join(_sources(record)),
                    "duration_minutes": record.get("duration_minutes"),
                    "distance_miles": record.get("distance_miles"),
                    "mean_hr_bpm": (record.get("heart_rate") or {}).get("mean_bpm"),
                    "daily_recovery_hrv_ms": (record.get("hrv") or {}).get("hrv_ms"),
                }
                for record in records
            ],
            width="stretch",
            hide_index=True,
        )


def _results_panel() -> None:
    st.subheader("API results")
    for title, path, key in (
        ("Metrics", "/metrics", "metrics"),
        ("Analysis", "/analysis", "results"),
    ):
        with st.expander(title, expanded=True):
            try:
                payload = _get(path)
                result = payload[key]
                if not isinstance(result, dict):
                    raise ValueError(f"{path} returned an invalid {key} object")
                if result:
                    if path == "/metrics":
                        quality = result.get("quality")
                        displayed: set[str] = set()
                        if isinstance(quality, dict):
                            st.markdown("**Data quality**")
                            st.json(quality)
                            displayed.add("quality")
                            for warning in quality.get("warnings") or []:
                                st.warning(str(warning))
                        analysis = result.get("analysis")
                        if isinstance(analysis, dict):
                            st.markdown("**Analysis metrics**")
                            st.json(analysis)
                            displayed.add("analysis")
                        remaining = {name: value for name, value in result.items() if name not in displayed}
                        if remaining:
                            st.json(remaining)
                    else:
                        st.json(result)
                else:
                    st.info(f"No {title.lower()} are available yet.")
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                st.error(f"{title} unavailable: {_api_error(exc)}")


def _pain_report_panel(report: dict[str, Any]) -> None:
    st.subheader("Physiological and Pain-Associated Burden")
    st.caption("Activity mode is not a pain label. Measured telemetry, subject reports, and proposed mechanisms have separate evidence roles.")
    observed = report.get("observed_physiological_burden") or {}
    st.markdown("**Measured physiological burden**")
    for finding in observed.get("findings") or []:
        st.write(f"• {finding}")
    ids = observed.get("evidence_ids") or []
    st.caption("Telemetry evidence IDs: " + (", ".join(map(str, ids)) if ids else "none cited"))
    biomechanical = report.get("biomechanical_interpretation") or {}
    st.markdown("**Potential biomechanical pain mechanisms**")
    st.write(biomechanical.get("mechanism") or "No mechanism assessed.")
    for item in biomechanical.get("supporting_evidence") or []:
        st.write(f"Supporting context: {item}")
    psychological = report.get("psychological_component") or {}
    st.markdown("**Psychological and autonomic considerations**")
    for item in psychological.get("potential_mechanisms") or []:
        st.write(f"• {item}")
    st.caption("Direct psychological evidence: " + ("; ".join(psychological.get("direct_evidence") or []) or "none in this dataset"))
    mobility = report.get("mobility_aid_implications") or {}
    st.markdown("**Functional implications for mobility**")
    st.write(mobility.get("functional_interpretation") or "No functional interpretation available.")
    pain = report.get("pain_association") or {}
    st.markdown("**Evidence strength**")
    st.write("Evidence strength: " + (pain.get("evidence_strength") or "insufficient") +
             " · Interpretive confidence: " + (pain.get("confidence") or "insufficient"))
    st.write(pain.get("reasoning") or "")
    uncertainty = report.get("uncertainty") or {}
    st.markdown("**Uncertainty and limitations**")
    for item in (uncertainty.get("missing_data") or []) + (uncertainty.get("causal_limitations") or []) + (psychological.get("limitations") or []):
        st.write(f"• {item}")


def _ask_panel() -> None:
    st.subheader("Ask the evidence")
    example = st.selectbox("Example questions", ["Choose an example"] + PAIN_EXAMPLES)
    with st.form("ask_form"):
        question = st.text_input("Question", value=example if example != "Choose an example" else "", placeholder="Ask about measured burden or a proposed pain mechanism")
        submitted = st.form_submit_button("Ask", type="primary")
    if submitted:
        if not question.strip():
            st.warning("Enter a question first.")
        else:
            try:
                st.session_state["last_answer"] = _post(
                    "/ask",
                    json={"question": question.strip(), "session_id": st.session_state["session_id"]},
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                st.error(f"Question failed: {_api_error(exc)}")

    answer = st.session_state.get("last_answer")
    if not answer:
        return
    st.markdown(answer.get("answer") or "No answer returned.")
    if isinstance(answer.get("report"), dict):
        _pain_report_panel(answer["report"])
    specialist = answer.get("specialist")
    latency = _number(answer.get("latency_ms"))
    st.caption(f"Specialist: {specialist or 'Unknown'}" + (f" · {latency:.0f} ms" if latency is not None else ""))
    if answer.get("metrics"):
        with st.expander("Answer metrics"):
            st.json(answer["metrics"])
    if answer.get("evidence_ids"):
        st.caption("Evidence record IDs: " + ", ".join(map(str, answer["evidence_ids"])))
    if answer.get("limitations"):
        st.warning("Limitations: " + " ".join(map(str, answer["limitations"])))
    if answer.get("validation_reasons"):
        st.caption("Evidence validator: " + "; ".join(map(str, answer["validation_reasons"])))
    with st.expander("Execution trace", expanded=True):
        trace = answer.get("execution_trace") or []
        if trace:
            for step in trace:
                st.write(str(step))
        else:
            st.info("The API returned no execution trace for this answer.")


def _upload_panel() -> None:
    st.subheader("Upload telemetry")
    uploaded = st.file_uploader("Choose a telemetry CSV", type=["csv"])
    if st.button("Ingest file", disabled=uploaded is None):
        try:
            payload = _post(
                "/ingest",
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")},
            )
            st.session_state["last_ingest"] = payload
            _get.clear()
            st.success(f"Saved {payload['records_saved']} records.")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            st.error(f"Ingest failed: {_api_error(exc)}")
    report = st.session_state.get("last_ingest")
    if report:
        with st.expander("Latest ingest quality report", expanded=True):
            st.json(report.get("quality") or {})
            for warning in (report.get("quality") or {}).get("warnings", []):
                st.warning(str(warning))


def _episode_panel() -> None:
    with st.expander("Episode Evidence and Llama Reasoning"):
        try:
            preview = _get("/episode")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            st.info(f"Episode preview unavailable: {_api_error(exc)}")
            return
        evidence = preview.get("evidence") or {}
        st.write("Selected episode", evidence.get("episode") or {})
        st.write("Telemetry", {"physiology": evidence.get("physiology"), "mechanics": evidence.get("mechanics")})
        st.write("Deterministic calculations", preview.get("calculations") or {})
        st.caption("Generic Commuting is not a verified ParaTransit ride. Subject context is reported history; episode pain and vehicle mechanics are unmeasured.")
        st.json(evidence)
        if st.button("Analyze Episode with Llama", type="primary"):
            try:
                st.session_state["episode_live_audit"] = _post("/episode/analyze")
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                st.error(f"Live episode analysis failed: {_api_error(exc)}")
        audit = st.session_state.get("episode_live_audit")
        if audit:
            st.markdown("**Exact model prompt and inference parameters**")
            st.json(audit.get("request") or {})
            if audit.get("retry_request"):
                st.markdown("**Exact validation-retry prompt**")
                st.json(audit["retry_request"])
            st.markdown("**Raw live model response**")
            st.json(audit.get("raw_response") or {})
            if audit.get("retry_raw_response"):
                st.markdown("**Raw validation-retry model response**")
                st.json(audit["retry_raw_response"])
            candidate = audit.get("candidate") or {}
            association = candidate.get("pain_association") or {}
            st.write("Supporting evidence", association.get("supporting_evidence") or [])
            st.write("Conflicting evidence", association.get("conflicting_evidence") or [])
            st.write("Confidence", candidate.get("confidence") or {})
            st.write("Limitations", candidate.get("limitations") or [])
            st.write("Evidence validation", audit.get("validation") or {})
            st.write("Execution trace", audit.get("execution_trace") or [])


def main() -> None:
    st.set_page_config(page_title="Telemetry interview dashboard", layout="wide")
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = str(uuid4())
    st.title("Telemetry interview dashboard")
    st.caption(f"FastAPI: {API_BASE_URL}")
    with st.sidebar:
        st.header("Data controls")
        if st.button("Refresh API data"):
            _get.clear()
            st.rerun()
        _upload_panel()
    _telemetry_panel()
    _results_panel()
    _ask_panel()
    _episode_panel()


if __name__ == "__main__":
    main()
