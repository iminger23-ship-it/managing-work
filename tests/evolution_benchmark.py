"""Fast local capability benchmark for self-evolution candidates."""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run() -> dict:
    checks = []

    def check(name, fn, weight):
        started = time.perf_counter()
        try:
            value = fn()
            ok = bool(value)
            detail = "ok" if ok else "returned false"
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        checks.append({"name": name, "ok": ok, "weight": weight, "elapsed_ms": elapsed_ms, "detail": detail})

    def import_router():
        return importlib.import_module("core.router")

    def import_live():
        return importlib.import_module("services.live_research")

    def import_learning():
        return importlib.import_module("core.learning")

    def import_registry():
        return importlib.import_module("tools.registry")

    check("router_import", lambda: import_router() is not None, 20)
    check("live_research_import", lambda: import_live() is not None, 15)
    check("learning_import", lambda: import_learning() is not None, 10)
    check("tools_import", lambda: import_registry() is not None, 10)

    class Stub:
        def is_reset_command(self, _): return False
        def matches_learning_command(self, _): return False
        def matches_external_command(self, _): return False
        def has_pending_action(self): return False
        def matches_memory_command(self, _): return False
        def matches_file_command(self, _): return False
        def matches_scan_command(self, _): return False
        def matches_action(self, _): return False
        def route_command(self, text):
            return "open_app" if text.lower() == "open spotify" else None

    def route_behavior():
        router = import_router().IntentRouter()
        intent = router.classify("open spotify", Stub())
        return intent.name == "PC_COMMAND" and intent.confidence >= 0.9

    def current_web_behavior():
        live = import_live()
        return live.should_search("what is the latest AI research?", True, True) is True

    check("open_command_routing", route_behavior, 25)
    check("current_web_detection", current_web_behavior, 10)

    def learning_behavior():
        learning = import_learning()
        return learning.LearningStore.normalize("  Hello   World ") == "hello world"

    check("learning_normalization", learning_behavior, 10)

    total = sum(c["weight"] for c in checks if c["ok"])
    # Map deterministic capability checks into the long-term objective model.
    passed = sum(1 for c in checks if c["ok"])
    category_scores = {
        "intelligence": round((1.0 if import_router() else 0.0) * 100, 2),
        "knowledge": round((1.0 if current_web_behavior() else 0.0) * 100, 2),
        "tool_use": round((1.0 if route_behavior() else 0.0) * 100, 2),
        "efficiency": round(max(0.0, 100.0 - min(100.0, sum(c["elapsed_ms"] for c in checks) / 20.0)), 2),
        "reliability": round((passed / max(1, len(checks))) * 100.0, 2),
        "personalization": round((1.0 if learning_behavior() else 0.0) * 100, 2),
    }
    return {
        "score": int(total),
        "max_score": 100,
        "checks": checks,
        "objective_scores": category_scores,
        "project": os.path.abspath(os.getcwd()),
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["score"] >= 90 else 1)
