"""Streamlit interview dashboard for the telemetry FastAPI service.

Run with ``streamlit run app/ui.py`` and set API_BASE_URL when the API is remote.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import os
from typing import Any
from uuid import uuid4

import httpx
import plotly.graph_objects as go
import streamlit as st


API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 30.0


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


def _coverage_warnings(records: list[dict[str, Any]], total: int) -> None:
    if total > len(records):
        st.warning("Only part of the telemetry collection was returned. Coverage checks use the displayed records.")
    if not any("walk" in str(record.get("activity") or "").lower() for record in records):
        st.warning("No walking observations appear in the returned telemetry. Walking comparisons are unavailable.")
    if not any("polar" in source.lower() for record in records for source in _sources(record)):
        st.warning("No Polar observations appear in the returned telemetry. Device comparisons are unavailable.")
    st.info("HRV values, when present, are WHOOP daily recovery context; they are not activity HRV measurements.")


def _chart_activity(records: list[dict[str, Any]]) -> None:
    counts = Counter(str(record.get("activity") or "Unknown") for record in records)
    if not counts:
        return
    labels, values = zip(*counts.most_common())
    figure = go.Figure(go.Bar(x=list(labels), y=list(values), marker_color="#2b7a78"))
    figure.update_layout(
        title="Activity records",
        xaxis_title="Activity",
        yaxis_title="Records",
        yaxis=dict(dtick=1),
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(figure, use_container_width=True)


def _chart_heart_rate(records: list[dict[str, Any]]) -> None:
    points: list[tuple[datetime, float, str, str]] = []
    for record in records:
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
    st.plotly_chart(figure, use_container_width=True)


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

    st.metric("Records reported by API", total)
    _coverage_warnings(records, total)
    if not records:
        st.info("No telemetry records are available yet. Upload a supported file to ingest data.")
        return

    left, right = st.columns(2)
    with left:
        _chart_activity(records)
    with right:
        _chart_heart_rate(records)

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
            use_container_width=True,
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


def _ask_panel() -> None:
    st.subheader("Ask the specialists")
    with st.form("ask_form"):
        question = st.text_input("Question", placeholder="What does the observed training data show?")
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
    with st.expander("Execution trace", expanded=True):
        trace = answer.get("execution_trace") or []
        if trace:
            for step in trace:
                st.write(str(step))
        else:
            st.info("The API returned no execution trace for this answer.")


def _upload_panel() -> None:
    st.subheader("Upload telemetry")
    uploaded = st.file_uploader("Choose a telemetry file", type=["csv", "json"])
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


if __name__ == "__main__":
    main()
