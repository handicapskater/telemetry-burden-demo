"""Question router and grounded specialists backed by LangGraph memory."""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.analysis import analyze_records
from app.inference import NUMBER, local_completion, validate_evidence


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
    return [category for category in categories if re.search(r"(?<!\w)" + re.escape(category.replace("_", " ")) + r"(?!\w)", normalized)]


def _analysis_specialist(state: AnalysisState) -> AnalysisState:
    result = analyze_records(state["records"])
    activities = result["activities"]
    question = state["question"].lower()
    pain_question = "pain" in question
    mobility_question = bool(re.search(r"\b(mobility aid|alternative mobility|skate.vs.walk|skating versus walking)\b", question))
    selected = _mentioned_categories(question, result["observed_activities"])
    missing = []
    if re.search(r"\bwalk(?:ing|s)?\b", question) and "walking" not in activities:
        missing.append("walking")
    if not selected and not missing and not pain_question and not mobility_question:
        selected = sorted(activities, key=lambda name: (-activities[name]["count"], name))[:3]
    evidence = []
    for category in selected:
        evidence.extend(activities[category]["evidence_ids"][:5])
    evidence = list(dict.fromkeys(evidence))
    if pain_question:
        draft = "No: elevated HR or altered HRV cannot independently prove pain. Exertion, fitness, medication, and temperature can also affect these measurements. The supplied workout summaries cannot establish a pain cause."
    elif mobility_question:
        draft = "An alternative mobility aid may be beneficial in general if it helps a person move with less effort or better access. These records do not establish that benefit. No walking observations support a measured skating versus walking conclusion."
    elif missing:
        draft = "No walking workouts are observed, so a walking comparison is unavailable."
        if selected:
            draft += " Observed requested categories: " + ", ".join(selected) + "."
    elif not selected:
        draft = "No workouts are available for analysis."
    else:
        clauses = []
        for category in selected:
            values = activities[category]
            clauses.append(
                f"{category}: {values['count']} workouts; mean duration {values['mean_duration_minutes']} minutes; "
                f"median duration {values['median_duration_minutes']} minutes; "
                f"mean workout HR {values['mean_heart_rate_bpm']} bpm; median workout HR {values['median_heart_rate_bpm']} bpm; "
                f"max workout HR {values['max_heart_rate_bpm']} bpm; mean burden proxy {values['mean_burden_proxy']} "
                f"({values['burden_classification']})"
            )
        draft = "; ".join(clauses) + "."
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


def _llama_node(state: AnalysisState) -> AnalysisState:
    if "No walking workouts are observed" in state["draft"]:
        return {"candidate": None, "inference_status": "local inference skipped: missing category abstention",
                "execution_trace": state["execution_trace"] + ["llama: abstention"]}
    candidate, status = local_completion(state["question"], state["draft"], state["evidence_ids"])
    return {"candidate": candidate, "inference_status": status,
            "execution_trace": state["execution_trace"] + ["llama: " + status]}


def _validator_node(state: AnalysisState) -> AnalysisState:
    candidate = state.get("candidate")
    if candidate is not None:
        if not validate_evidence(candidate, allowed_numbers=set(NUMBER.findall(state["draft"])), allowed_ids=set(state["evidence_ids"])):
            candidate = None
        else:
            answer = candidate["answer"]
            if ("HRV" in state["draft"] and "HRV" not in answer) or ("burden" in state["draft"] and "burden" not in answer):
                candidate = None
            if "No Polar" in state["draft"] and not re.search(r"\bno\b.{0,40}\bPolar\b", answer, re.I):
                candidate = None
            if "No activity RMSSD" in state["draft"] and not re.search(r"\bno\b.{0,40}\bRMSSD\b", answer, re.I):
                candidate = None
            if "cannot independently prove pain" in state["draft"] and not re.search(r"\b(cannot|can't|does not|doesn't|no proof|not proof)\b.*\bpain\b", answer, re.I):
                candidate = None
            if "cannot independently prove pain" in state["draft"] and not all(term in answer.lower() for term in ("exertion", "fitness", "medication", "temperature")):
                candidate = None
            if "No walking observations support" in state["draft"] and not ("walking" in answer.lower() and re.search(r"\b(no|cannot|unavailable|not)\b", answer, re.I)):
                candidate = None
            if "An alternative mobility aid may" in state["draft"] and "general" not in answer.lower():
                candidate = None
    if candidate is None:
        return {"answer": "[Deterministic fallback] " + state["draft"],
                "execution_trace": state["execution_trace"] + ["evidence_validator: deterministic fallback"]}
    return {"answer": candidate["answer"], "evidence_ids": candidate["evidence_ids"],
            "limitations": list(dict.fromkeys(state["limitations"] + candidate.get("limitations", []))),
            "execution_trace": state["execution_trace"] + ["evidence_validator: accepted"]}


def _build_graph():
    builder = StateGraph(AnalysisState)
    builder.add_node("supervisor", _supervisor)
    builder.add_node("data_specialist", _data_specialist)
    builder.add_node("analysis_specialist", _analysis_specialist)
    builder.add_node("llama", _llama_node)
    builder.add_node("evidence_validator", _validator_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", lambda state: state["route"],
                                  {"data": "data_specialist", "analysis": "analysis_specialist"})
    builder.add_edge("data_specialist", "llama")
    builder.add_edge("analysis_specialist", "llama")
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
        "execution_trace": state["execution_trace"],
        "latency_ms": round((perf_counter() - started) * 1000, 2),
    }
