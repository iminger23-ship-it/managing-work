from __future__ import annotations

import os
import statistics
import time
from typing import Any

OBJECTIVE_WEIGHTS = {
    "intelligence": 30.0,
    "knowledge": 20.0,
    "tool_use": 20.0,
    "efficiency": 15.0,
    "reliability": 10.0,
    "personalization": 5.0,
}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _mean_or(default: float, values: list[float]) -> float:
    return statistics.mean(values) if values else default


def snapshot(learning_store=None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Calculate measurable user-facing objectives from local telemetry.

    These are evidence metrics, not claims about model intelligence. Metrics with
    insufficient samples are marked as provisional so evolution cannot confuse
    missing data with a perfect score.
    """
    base = {
        "intelligence": {"score": 0.0, "confidence": "low", "metrics": {}},
        "knowledge": {"score": 0.0, "confidence": "low", "metrics": {}},
        "tool_use": {"score": 0.0, "confidence": "low", "metrics": {}},
        "efficiency": {"score": 0.0, "confidence": "low", "metrics": {}},
        "reliability": {"score": 0.0, "confidence": "low", "metrics": {}},
        "personalization": {"score": 0.0, "confidence": "low", "metrics": {}},
    }
    if learning_store is None:
        return finalize(base, extra=extra)

    try:
        data = learning_store.objective_telemetry()
    except Exception:
        return finalize(base, extra=extra)

    # Intelligence: explicit positive feedback on model answers, when enough
    # feedback exists. Accuracy is intentionally labeled feedback_accuracy rather
    # than pretending thumbs-up is an objective benchmark.
    fb_total = int(data.get("ai_feedback_total", 0))
    fb_pos = int(data.get("ai_feedback_positive", 0))
    ai_accuracy = (fb_pos / fb_total * 100.0) if fb_total else 0.0
    base["intelligence"] = {
        "score": _clamp(ai_accuracy),
        "confidence": "high" if fb_total >= 10 else ("medium" if fb_total >= 3 else "low"),
        "metrics": {"answer_feedback_accuracy": round(ai_accuracy, 1), "feedback_samples": fb_total,
                    "coding_task_success": round(float(data.get("coding_success_rate", 0.0)), 1),
                    "coding_samples": int(data.get("coding_samples", 0))},
    }

    # Knowledge: live web research quality is measured by fetch success and
    # grounded-source usage. This is a proxy, not an independent fact-checker.
    web_attempts = int(data.get("web_attempts", 0))
    web_success = int(data.get("web_success", 0))
    web_quality = (web_success / web_attempts * 100.0) if web_attempts else 0.0
    base["knowledge"] = {
        "score": _clamp(web_quality),
        "confidence": "high" if web_attempts >= 10 else ("medium" if web_attempts >= 3 else "low"),
        "metrics": {"research_success_rate": round(web_quality, 1), "research_attempts": web_attempts,
                    "sources_fetched": int(data.get("sources_fetched", 0))},
    }

    cmd_total = int(data.get("pc_total", 0))
    cmd_ok = int(data.get("pc_success", 0))
    command_rate = cmd_ok / cmd_total * 100.0 if cmd_total else 0.0
    workflow_total = int(data.get("workflow_total", 0))
    workflow_ok = int(data.get("workflow_success", 0))
    workflow_rate = workflow_ok / workflow_total * 100.0 if workflow_total else command_rate
    launch_failures = int(data.get("launch_failures", 0))
    launch_total = int(data.get("launch_total", 0))
    launch_success = (1.0 - launch_failures / launch_total) * 100.0 if launch_total else command_rate
    tool_score = _mean_or(0.0, [command_rate, workflow_rate, launch_success])
    base["tool_use"] = {
        "score": _clamp(tool_score),
        "confidence": "high" if cmd_total >= 20 else ("medium" if cmd_total >= 5 else "low"),
        "metrics": {"command_success_rate": round(command_rate, 1),
                    "failed_launches": launch_failures,
                    "launch_success_rate": round(launch_success, 1),
                    "workflow_completion_rate": round(workflow_rate, 1)},
    }

    durations = [float(x) for x in data.get("response_durations_ms", []) if float(x) >= 0]
    median_ms = statistics.median(durations) if durations else 0.0
    # 1500 ms is the reference target for a lightweight local orchestration turn.
    latency_score = _clamp(100.0 * 1500.0 / max(1500.0, median_ms)) if median_ms else 0.0
    rss_mb = float(data.get("rss_mb", 0.0) or 0.0)
    memory_score = _clamp(100.0 * 1024.0 / max(1024.0, rss_mb)) if rss_mb else 0.0
    token_eff = float(data.get("token_context_efficiency", 0.0) or 0.0)
    cpu_pct = float(data.get("cpu_pct", 0.0) or 0.0)
    gpu_pct = float(data.get("gpu_pct", 0.0) or 0.0)
    # Runtime utilization is reported directly; the score uses the explicit
    # latency/memory/context proxies so resource spikes remain visible instead
    # of being hidden inside a single opaque number.
    efficiency_score = _mean_or(0.0, [latency_score, memory_score, token_eff])
    base["efficiency"] = {
        "score": _clamp(efficiency_score),
        "confidence": "medium" if durations else "low",
        "metrics": {"median_response_ms": round(median_ms, 1),
                    "rss_mb": round(rss_mb, 1), "cpu_pct": round(cpu_pct, 1), "gpu_pct": round(gpu_pct, 1),
                    "token_context_efficiency": round(token_eff, 1),
                    "avg_context_chars": round(float(data.get("avg_context_chars", 0.0) or 0.0), 1),
                    "avg_output_chars": round(float(data.get("avg_output_chars", 0.0) or 0.0), 1)},
    }

    total = int(data.get("all_total", 0))
    successful = int(data.get("all_success", 0))
    reliability = successful / total * 100.0 if total else 0.0
    base["reliability"] = {
        "score": _clamp(reliability),
        "confidence": "high" if total >= 50 else ("medium" if total >= 10 else "low"),
        "metrics": {"interaction_success_rate": round(reliability, 1), "interactions": total,
                    "regressions": int(data.get("evolution_regressions", 0))},
    }

    lessons = int(data.get("lessons", 0))
    approved = int(data.get("approved_skills", 0))
    predictions = int(data.get("habit_predictions", 0))
    repeat_reduction = float(data.get("repeat_task_reduction", 0.0) or 0.0)
    habit_success = float(data.get("habit_prediction_success_rate", 0.0) or 0.0)
    personalization = _mean_or(0.0, [
        _clamp(min(100.0, lessons * 10.0)),
        _clamp(min(100.0, approved * 20.0)),
        _clamp(habit_success),
        _clamp(repeat_reduction),
    ])
    base["personalization"] = {
        "score": _clamp(personalization),
        "confidence": "high" if predictions >= 10 else ("medium" if predictions >= 3 else "low"),
        "metrics": {"useful_learned_skills": approved, "habit_predictions": predictions,
                    "habit_prediction_success_rate": round(habit_success, 1),
                    "repeated_task_reduction": round(repeat_reduction, 1)},
    }
    return finalize(base, extra=extra)


def finalize(categories: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    weighted = 0.0
    available = 0.0
    for name, weight in OBJECTIVE_WEIGHTS.items():
        item = categories.get(name, {})
        score = float(item.get("score", 0.0))
        conf = item.get("confidence", "low")
        if conf != "low":
            weighted += score * weight
            available += weight
    composite = weighted / available if available else 0.0
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "composite_score": round(composite, 2),
        "coverage": round(available, 1),
        "weights": OBJECTIVE_WEIGHTS.copy(),
        "categories": categories,
    }
    if extra:
        out["extra"] = extra
    return out


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    b = float(before.get("composite_score", 0.0))
    a = float(after.get("composite_score", 0.0))
    delta = a - b
    pct = (delta / b * 100.0) if b else (100.0 if a else 0.0)
    category_delta = {}
    for name in OBJECTIVE_WEIGHTS:
        bs = float(before.get("categories", {}).get(name, {}).get("score", 0.0))
        as_ = float(after.get("categories", {}).get(name, {}).get("score", 0.0))
        category_delta[name] = round(as_ - bs, 2)
    return {"before": round(b, 2), "after": round(a, 2), "delta": round(delta, 2),
            "percent": round(pct, 2), "category_delta": category_delta}
