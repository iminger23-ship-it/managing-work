from __future__ import annotations

"""Long-running MyLocalAI autonomous research/evolution worker.

This process is intentionally separate from the GUI so it can continue running
when the desktop chat window is closed. It researches the public web, observes
foreground-app/workflow metadata, asks the local model for improvement ideas,
generates candidate source changes, and keeps only candidates that pass the
existing validation/benchmark gate.

The worker never executes downloaded code from the web. Web content is treated
as reference material. Self-generated source changes are validated in an
isolated copy before application and the existing SelfEvolution service makes
a rollback backup before replacing project files.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
# When this file is launched directly (python services\autonomous_lab.py),
# Python puts only the services directory on sys.path. Add the project root
# before importing sibling packages such as services.* and core.*.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
DATA_DIR = APP_DIR / "data"
LOG_DIR = APP_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "autonomous_lab.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
)
log = logging.getLogger("mylocalai.autonomous_lab")

STATE_PATH = DATA_DIR / "autonomous_lab.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "research_interval_hours": 2.0,
    "evolution_interval_hours": 6.0,
    "screen_interval_seconds": 8.0,
    "research_on_start": True,
    "evolve_on_start": False,
    "max_research_results": 6,
    "max_research_pages": 5,
}

DISCOVERY_QUERIES = [
    "latest AI agent research coding agents software engineering 2026",
    "latest autonomous coding agent evaluation benchmark 2026",
    "latest computer use vision agent research 2026",
    "latest local AI inference efficiency memory context KV cache 2026",
    "latest agent planning reasoning tool use research 2026",
    "latest AI self improvement evaluation research 2026",
    "latest open source AI coding tools agents 2026",
    "latest multimodal UI grounding screen parsing research 2026",
]

_STOP = threading.Event()


def _read_state() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    try:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError:
        log.exception("Unable to save autonomous lab state")


def _cfg() -> dict:
    state = _read_state()
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(state.get("config") or {})
    cfg["research_interval_hours"] = max(0.5, min(168.0, float(cfg["research_interval_hours"])))
    cfg["evolution_interval_hours"] = max(0.5, min(168.0, float(cfg["evolution_interval_hours"])))
    cfg["screen_interval_seconds"] = max(2.0, min(60.0, float(cfg["screen_interval_seconds"])))
    cfg["max_research_results"] = max(2, min(12, int(cfg["max_research_results"])))
    cfg["max_research_pages"] = max(1, min(8, int(cfg["max_research_pages"])))
    return cfg


def _record(**updates) -> dict:
    state = _read_state()
    state.update(updates)
    state["config"] = _cfg()
    _write_state(state)
    return state


def _pid_running(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import psutil
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def status() -> dict:
    state = _read_state()
    state["config"] = _cfg()
    pid = state.get("pid")
    running = _pid_running(pid)
    state["running"] = running
    if running:
        state["message"] = "Autonomous AI Lab is running in the background."
    elif state.get("installed"):
        state["message"] = "Autonomous AI Lab is installed but not currently running."
    return state


def _research_once(research_manager):
    cfg = _cfg()
    started = time.perf_counter()
    results = research_manager.refresh(max_results_per_query=cfg["max_research_results"])
    elapsed = int((time.perf_counter() - started) * 1000)
    state = _read_state()
    state["last_research_at"] = datetime.now().isoformat(timespec="seconds")
    state["last_research_elapsed_ms"] = elapsed
    state["last_research_result"] = results
    state["total_research_cycles"] = int(state.get("total_research_cycles", 0)) + 1
    state["next_research_at"] = (datetime.now() + timedelta(hours=cfg["research_interval_hours"])).isoformat(timespec="seconds")
    _write_state(state)
    log.info("Research cycle complete: %s", results)
    return results


def _evolve_once(engine=None):
    """Run the expensive engine/evolution stage lazily."""
    if engine is None:
        import pc_ai_engine as engine
    started = time.perf_counter()
    diagnosis = engine._evolution_diagnosis()
    result = engine._SELF_EVOLUTION.evolve(diagnosis, autonomous=True)
    elapsed = int((time.perf_counter() - started) * 1000)
    state = _read_state()
    state["last_evolution_at"] = datetime.now().isoformat(timespec="seconds")
    state["last_evolution_elapsed_ms"] = elapsed
    state["last_evolution_result"] = {
        "status": result.get("status"),
        "message": result.get("message"),
        "objective_comparison": result.get("objective_comparison"),
        "benchmark": result.get("benchmark"),
        "proposal": (result.get("proposal") or {}).get("id"),
    }
    state["total_evolution_cycles"] = int(state.get("total_evolution_cycles", 0)) + 1
    cfg = _cfg()
    state["next_evolution_at"] = (datetime.now() + timedelta(hours=cfg["evolution_interval_hours"])).isoformat(timespec="seconds")
    if result.get("status") == "applied":
        state["last_successful_evolution_at"] = state["last_evolution_at"]
    _write_state(state)
    log.info("Evolution cycle complete: status=%s result=%s", result.get("status"), result)
    return result


def _research_discovery(knowledge_store):
    """Refresh one rotating topic slice without importing the full engine."""
    from services.internet import search

    cfg = _cfg()
    state = _read_state()
    offset = int(state.get("discovery_offset", 0))
    query = DISCOVERY_QUERIES[offset % len(DISCOVERY_QUERIES)]
    try:
        rows = search(query, cfg["max_research_results"])
    except Exception:
        rows = []
    indexed = 0
    chunks = 0
    for row in rows[: cfg["max_research_pages"]]:
        try:
            _title, _url, count = knowledge_store.ingest_url(row["url"], row.get("title") or row["url"])
            if count:
                indexed += 1
                chunks += count
        except Exception:
            log.exception("Discovery source ingest failed: %s", row.get("url"))
    state["discovery_offset"] = (offset + 1) % len(DISCOVERY_QUERIES)
    state["last_discovery"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "query": query,
        "searched": len(rows),
        "indexed": indexed,
        "chunks": chunks,
    }
    _write_state(state)
    log.info("Discovery cycle: %s", state["last_discovery"])


def run() -> int:
    # Mark this process so pc_ai_engine does not create a second research worker.
    os.environ["MYLOCALAI_AUTONOMOUS_LAB"] = "1"
    _record(installed=True, running=True, pid=os.getpid(), started_at=datetime.now().isoformat(timespec="seconds"), message="Autonomous AI Lab is running.")
    # Bootstrap the lightweight services only. The full engine (and its model
    # dependencies) is intentionally imported only when an evolution cycle is due.
    try:
        from services.knowledge import KnowledgeStore
        from services.research import ResearchManager
        from services.screen_observer import ScreenObserver
        knowledge_store = KnowledgeStore(str(DATA_DIR))
        research_manager = ResearchManager(str(DATA_DIR), knowledge_store)
    except Exception as exc:
        log.exception("Could not initialize lightweight autonomous services")
        _record(running=False, pid=None, last_error=f"Autonomous services failed: {type(exc).__name__}: {exc}")
        return 2

    cfg = _cfg()
    screen_observer = ScreenObserver(str(DATA_DIR), interval=cfg["screen_interval_seconds"])
    try:
        screen_observer.start()
    except Exception:
        log.exception("Unable to start screen observer")

    if cfg.get("research_on_start", True):
        # Do not block lab startup on network/model work. The worker is already
        # marked running; research follows after a short startup grace period.
        if not _STOP.wait(3.0):
            try:
                _research_once(research_manager)
                _research_discovery(knowledge_store)
            except Exception:
                log.exception("Startup research failed")

    if cfg.get("evolve_on_start", False):
        try:
            _evolve_once()
        except Exception:
            log.exception("Startup evolution failed")

    last_research = 0.0
    last_evolution = 0.0
    last_discovery = 0.0
    cfg = _cfg()
    while not _STOP.wait(5.0):
        try:
            now = time.time()
            cfg = _cfg()
            if not cfg.get("enabled", True):
                continue
            if now - last_research >= cfg["research_interval_hours"] * 3600:
                _research_once(research_manager)
                last_research = now
                # A discovery query is a separate rotating slice from the curated refresh.
                if now - last_discovery >= 3600:
                    _research_discovery(knowledge_store)
                    last_discovery = now
            if now - last_evolution >= cfg["evolution_interval_hours"] * 3600:
                try:
                    _evolve_once()
                except Exception:
                    # An engine/model dependency failure must not kill research or screen learning.
                    log.exception("Evolution cycle failed")
                last_evolution = now
        except Exception:
            log.exception("Autonomous lab cycle failed")

    try:
        screen_observer.stop()
    except Exception:
        pass
    _record(running=False, pid=None, stopped_at=datetime.now().isoformat(timespec="seconds"), message="Autonomous AI Lab stopped.")
    return 0


def _shutdown(signum=None, frame=None):
    log.info("Shutdown requested (%s)", signum)
    _STOP.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)
    raise SystemExit(run())
