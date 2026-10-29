"""Question router and grounded specialists backed by LangGraph memory."""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.analysis import analyze_records
from app.evidence import build_evidence_payload, deterministic_report, ground_report
from app.inference import local_completion, validate_pain_report
from app.schemas import EvidencePayload, PainBurdenReport


class AnalysisState(TypedDict, total=False):
    question: str
    records: list[dict[str, Any]]
    route: str
    metrics: dict[str, Any]
    draft: str
    evidence_ids: list[str]
    limitations: list[str]
    execution_trace: list[str]
    candidate: dict[str, Any] | None
    inference_status: str
    answer: str
    evidence: dict[str, Any]
    report: dict[str, Any]
    validation_reasons: list[str]


def _supervisor(state: AnalysisState) -> AnalysisState:
    question = state["question"].lower()
    route = "data" if re.search(r"\b(source|sources|record|records|coverage|duplicate|provenance|available|availability|device|devices|measurement|measurements|polar|rmssd)\b", question) else "analysis"
    return {"route": route, "execution_trace": [f"supervisor → {route}_specialist"]}


def _data_specialist(state: AnalysisState) -> AnalysisState:
    result = analyze_records(state["records"])
    ids = [str(row["record_id"]) for row in state["records"][:10] if row.get("record_id")]
    has_polar = any("polar" in str(row.get("source", "")).lower() or any("polar" in str(source).lower() for source in row.get("sources", [])) for row in state["records"])
    has_rmssd = any((row.get("hrv") or {}).get("rmssd_ms") is not None for row in state["records"])
    draft = (
        f"The dataset has {result['input_rows']} WHOOP rows and {result['unique_workouts']} "
        f"unique workouts after removing {result['duplicate_rows']} repeated workout rows. "
        f"Observed categories: {', '.join(result['observed_activities']) or 'none'}. "
        "Available measurements include workout duration, mean and max workout heart rate, "
        "and WHOOP daily recovery HRV. "
        + ("Polar source records are present. " if has_polar else "No Polar source records are present. ")
        + ("RMSSD values are present. " if has_rmssd else "No activity RMSSD measurements are present. ")
        + "Daily HRV is not activity HRV. Evidence IDs are sample source rows."
    )
    return {"metrics": result, "draft": draft, "evidence_ids": ids,
            "limitations": result["limitations"], "execution_trace": state["execution_trace"] + ["data_specialist: deterministic dataset summary"]}


def _mentioned_categories(question: str, categories: list[str]) -> list[str]:
    normalized = question.lower().replace("_", " ").replace("-", " ")
    aliases = {"transportation": ("commute", "commuting", "transport", "transportation", "paratransit", "bus", "vehicle"),
               "skating": ("skate", "skates", "skating", "inline skate", "inline skating")}
    selected = []
    for category in categories:
        names = aliases.get(category, (category.replace("_", " "),))
        if any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", normalized) for name in names):
            selected.append(category)
    return selected


def _analysis_specialist(state: AnalysisState) -> AnalysisState:
    result = analyze_records(state["records"])
    activities = result["activities"]
    question = state["question"].lower()
    pain_question = bool(re.search(r"\b(prove pain|proves pain|mean.*more pain|causes more pain)\b", question))
    mobility_question = bool(re.search(r"\b(mobility aid|alternative mobility|skate.vs.walk|skating versus walking)\b", question))
    supported_question = bool(re.search(r"\b(hr|hrv|heart|burden|physiolog\w*|stress|pain|pelvic|walk\w*|skat\w*|commut\w*|transport\w*|paratransit|bus|vehicle|vibrat\w*|jolt\w*|mobility|activit\w*|duration|distance|recovery|strain|whoop|strava|polar|device\w*|record\w*|measur\w*)\b", question))
    highest_hr_question = bool(re.search(r"\b(highest|maximum|peak|max)\b", question) and re.search(r"\b(hr|heart rate)\b", question))
    selected = _mentioned_categories(question, result["observed_activities"])
    missing = []
    if re.search(r"\bwalk(?:ing|s)?\b", question) and "walking" not in activities:
        missing.append("walking")
    if highest_hr_question:
        eligible = [(name, values["max_heart_rate_bpm"]) for name, values in activities.items()
                    if values["max_heart_rate_bpm"] is not None]
        selected = [max(eligible, key=lambda item: item[1])[0]] if eligible else []
    elif not selected and not missing and not pain_question and not mobility_question and supported_question:
        selected = sorted(activities, key=lambda name: (-activities[name]["count"], name))[:3]
    evidence = []
    for category in selected:
        evidence.extend(activities[category]["evidence_ids"][:5])
    evidence = list(dict.fromkeys(evidence))
    if not supported_question:
        draft = "The supplied telemetry cannot answer this question. Ask about measured activities, heart rate, daily recovery HRV, or descriptive burden."
    elif pain_question:
        draft = "No: elevated HR or altered HRV cannot independently prove pain. Exertion, fitness, medication, and temperature can also affect these measurements. The supplied workout summaries cannot establish a pain cause."
    elif mobility_question:
        draft = "An alternative mobility aid may be beneficial in general if it helps a person move with less effort or better access. These records do not establish that benefit. No walking observations support a measured skating versus walking conclusion."
    elif missing:
        draft = "No walking workouts are observed, so a walking comparison is unavailable."
        if selected:
            draft += " Observed requested categories: " + ", ".join(selected) + "."
    elif not selected:
        draft = "No relevant heart-rate measurements are available for this question."
    else:
        clauses = []
        for category in selected:
            values = activities[category]
            clauses.append(
                f"{category}: {values['count']} workouts ({values['heart_rate_sample_count']} with mean HR); mean duration {values['mean_duration_minutes']} minutes; "
                f"median duration {values['median_duration_minutes']} minutes; "
                f"mean workout HR {values['mean_heart_rate_bpm']} bpm; median workout HR {values['median_heart_rate_bpm']} bpm; "
                f"max workout HR {values['max_heart_rate_bpm']} bpm; mean burden proxy {values['mean_burden_proxy']} "
                f"({values['burden_classification']})"
            )
        draft = "; ".join(clauses) + "."
        if highest_hr_question:
            draft = "Highest observed workout maximum HR: " + draft
        if len(selected) > 1:
            draft += " These are descriptive comparisons of observed categories."
            for comparison in result["comparisons"]:
                if {comparison["activity_a"], comparison["activity_b"]}.issubset(selected):
                    difference = comparison["median_hr_difference_b_minus_a"]
                    draft += (f" {comparison['activity_a']} versus {comparison['activity_b']} "
                              f"median HR signed difference (B minus A): {difference['signed_absolute']} bpm; "
                              f"{difference['signed_percent_of_first']}% relative to A.")
    if "hrv" in question:
        draft += " HRV is WHOOP daily recovery context, not activity HRV."
        for category in selected:
            values = activities[category]
            draft += f" {category} daily HRV mean: {values['mean_daily_hrv_ms']} ms across {values['daily_hrv_sample_count']} workouts."
    if re.search(r"\b(caus|improv|boost|effect|impact|predict|lead to)\w*", question):
        draft += " These observations cannot establish a causal effect."
    if selected and not missing:
        draft += " Burden proxy = duration in minutes × mean workout HR in bpm / 100; labels are descriptive heuristic thresholds, not clinical load or health risk. Duration is reported WHOOP workout duration, not moving time. Elevated-HR duration is unavailable without heart-rate time series."
    limitations = list(result["limitations"])
    if missing:
        limitations.append("No walking category is present in the supplied records.")
    return {"metrics": result, "draft": draft, "evidence_ids": evidence, "limitations": limitations,
            "execution_trace": state["execution_trace"] + ["analysis_specialist: deterministic category statistics"]}


def _biomechanical_stage(state: AnalysisState) -> AnalysisState:
    evidence = build_evidence_payload(state["question"], state["metrics"], state["records"])
    return {"evidence": evidence.model_dump(),
            "execution_trace": state["execution_trace"] + ["biomechanical_interpretation: measured exposure versus proposed loading"]}


def _autonomic_stage(state: AnalysisState) -> AnalysisState:
    evidence = EvidencePayload.model_validate(state["evidence"])
    note = "Daily recovery HRV is not an event-specific autonomic measurement"
    if note not in evidence.missing_data:
        evidence.missing_data.append(note)
    return {"evidence": evidence.model_dump(),
            "execution_trace": state["execution_trace"] + ["pain_autonomic_interpretation: HR and recovery HRV context"]}


def _psychological_stage(state: AnalysisState) -> AnalysisState:
    evidence = EvidencePayload.model_validate(state["evidence"])
    note = "No validated psychological assessment or synchronized stress self-report"
    if note not in evidence.missing_data:
        evidence.missing_data.append(note)
    return {"evidence": evidence.model_dump(),
            "execution_trace": state["execution_trace"] + ["psychological_interpretation: hypotheses and absent direct assessment"]}


def _llama_node(state: AnalysisState) -> AnalysisState:
    if "telemetry cannot answer this question" in state["draft"]:
        return {"candidate": None, "inference_status": "unsupported question",
                "execution_trace": state["execution_trace"] + ["llama: abstention"]}
    evidence = EvidencePayload.model_validate(state["evidence"])
    candidate, status = local_completion(state["question"], evidence)
    return {"candidate": candidate, "inference_status": status,
            "execution_trace": state["execution_trace"] + ["llama: " + status]}


def _validator_node(state: AnalysisState) -> AnalysisState:
    evidence = EvidencePayload.model_validate(state["evidence"])
    candidate = state.get("candidate")
    report: PainBurdenReport | None = None
    reasons: list[str] = []
    if candidate is not None:
        report, reasons = validate_pain_report(candidate, evidence, state["question"])
    if report is None:
        report = deterministic_report(state["question"], evidence)
        if candidate is None and state.get("inference_status"):
            reasons.append(state["inference_status"])
        answer = "[Deterministic fallback] " + (state["draft"] if "telemetry cannot answer this question" in state["draft"] else report.conclusion)
        if state.get("draft") and "telemetry cannot answer this question" not in state["draft"]:
            answer += "\n\nComputed evidence: " + state["draft"]
        status = "evidence_validator: deterministic fallback"
    else:
        report = ground_report(report, state["question"], evidence)
        answer = report.conclusion
        if report.observed_physiological_burden.findings:
            answer += "\n\nMeasured burden: " + " ".join(report.observed_physiological_burden.findings)
        status = "evidence_validator: accepted"
    limitations = list(dict.fromkeys(state["limitations"] + report.uncertainty.missing_data
                                      + report.uncertainty.causal_limitations + reasons))
    return {"answer": answer, "report": report.model_dump(),
            "evidence_ids": report.observed_physiological_burden.evidence_ids,
            "limitations": limitations, "validation_reasons": reasons,
            "execution_trace": state["execution_trace"] + [status]}


def _build_graph():
    builder = StateGraph(AnalysisState)
    builder.add_node("supervisor", _supervisor)
    builder.add_node("data_specialist", _data_specialist)
    builder.add_node("analysis_specialist", _analysis_specialist)
    builder.add_node("biomechanical_interpretation", _biomechanical_stage)
    builder.add_node("pain_autonomic_interpretation", _autonomic_stage)
    builder.add_node("psychological_interpretation", _psychological_stage)
    builder.add_node("llama", _llama_node)
    builder.add_node("evidence_validator", _validator_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", lambda state: state["route"],
                                  {"data": "data_specialist", "analysis": "analysis_specialist"})
    builder.add_edge("data_specialist", "biomechanical_interpretation")
    builder.add_edge("analysis_specialist", "biomechanical_interpretation")
    builder.add_edge("biomechanical_interpretation", "pain_autonomic_interpretation")
    builder.add_edge("pain_autonomic_interpretation", "psychological_interpretation")
    builder.add_edge("psychological_interpretation", "llama")
    builder.add_edge("llama", "evidence_validator")
    builder.add_edge("evidence_validator", END)
    return builder.compile(checkpointer=MemorySaver())


_GRAPH = _build_graph()


def answer_question(question: str, session_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Answer using grounded statistics, a conditional specialist, and optional local LLM."""
    started = perf_counter()
    if not question.strip():
        raise ValueError("question must not be empty")
    state = _GRAPH.invoke(
        {"question": question, "records": records},
        config={"configurable": {"thread_id": session_id or "default"}},
    )
    return {
        "answer": state["answer"],
        "specialist": state["route"],
        "metrics": state["metrics"],
        "evidence_ids": state["evidence_ids"],
        "limitations": state["limitations"],
        "validation_reasons": state["validation_reasons"],
        "execution_trace": state["execution_trace"],
        "latency_ms": round((perf_counter() - started) * 1000, 2),
        "report": state["report"],
    }
