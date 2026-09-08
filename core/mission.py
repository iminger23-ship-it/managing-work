"""MyLocalAI's persistent improvement mission."""
from __future__ import annotations

PRIME_DIRECTIVE = (
    "Continuously make MyLocalAI smarter, more capable, more reliable, and more efficient "
    "by learning from evidence, current research, user feedback, screen/workflow patterns, "
    "and measured task outcomes; prefer changes that measurably improve the AI system over "
    "changes that merely increase complexity or autonomy."
)

OBJECTIVES = (
    ("intelligence", 30, "Improve reasoning, planning, context use, and model orchestration."),
    ("knowledge", 20, "Improve fresh research, source quality, retrieval, and grounded answers."),
    ("tool_use", 20, "Improve routing, tools, workflows, and task completion accuracy."),
    ("efficiency", 15, "Reduce latency, waste, repeated work, and unnecessary context."),
    ("reliability", 10, "Reduce errors and regressions while preserving existing capabilities."),
    ("personalization", 5, "Learn useful user habits and preferences without guessing sensitive data."),
)

MEASURABLE_METRICS = {
    "intelligence": ["answer_accuracy", "research_grounding", "coding_task_success"],
    "efficiency": ["response_time", "cpu_gpu_ram", "token_context_efficiency"],
    "tool_use": ["command_success_rate", "failed_launches", "workflow_completion"],
    "personalization": ["useful_learned_skills", "repeated_task_reduction", "habit_prediction_success"],
}

SUCCESS_RULE = (
    "An evolution is successful only when the candidate passes validation and does not regress "
    "the measurable objective score. Prefer real before/after evidence, and never substitute a "
    "hypothetical execution result for an actual test."
)


def mission_context() -> str:
    lines = ["PRIME DIRECTIVE:", PRIME_DIRECTIVE, "", "OPTIMIZATION OBJECTIVES:"]
    for name, weight, description in OBJECTIVES:
        lines.append(f"- {name} ({weight}%): {description}")
    lines.extend(["", "MEASURABLE METRICS:"])
    for name, metrics in MEASURABLE_METRICS.items():
        lines.append(f"- {name}: " + ", ".join(metrics))
    lines.extend(["", "SUCCESS RULE:", SUCCESS_RULE])
    return "\n".join(lines)
