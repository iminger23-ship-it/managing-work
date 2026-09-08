"""Local learning, habit detection, and safe evolution for MyLocalAI.

The learning layer stores interactions, feedback, explicit lessons, and
behavioral observations in SQLite. It can *propose* new shortcuts/workflows
when repeated successful patterns are detected. Nothing is auto-promoted:
proposals require explicit approval and promoted workflows are still executed
through the normal deterministic safety/action router.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class LearningStore:
    """Small local SQLite store for durable, inspectable learning."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "learning.db"
        self._lock = threading.RLock()
        self._init_db()
        self._starts: dict[int, float] = {}

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    source TEXT,
                    intent TEXT,
                    output_text TEXT,
                    success INTEGER,
                    feedback INTEGER,
                    feedback_text TEXT,
                    duration_ms REAL
                );
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    command TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    uses INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    last_used TEXT,
                    UNIQUE(trigger)
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    action_key TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS evolution_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    candidate_type TEXT NOT NULL,
                    proposed_trigger TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    positive_count INTEGER NOT NULL DEFAULT 0,
                    negative_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    UNIQUE(candidate_type, proposed_trigger, steps_json)
                );
                CREATE INDEX IF NOT EXISTS idx_lessons_enabled ON lessons(enabled);
                CREATE INDEX IF NOT EXISTS idx_interactions_created ON interactions(created_at);
                CREATE INDEX IF NOT EXISTS idx_observations_created ON observations(created_at);
                CREATE INDEX IF NOT EXISTS idx_candidates_status ON evolution_candidates(status);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(interactions)").fetchall()}
            if "duration_ms" not in columns:
                db.execute("ALTER TABLE interactions ADD COLUMN duration_ms REAL")

    @staticmethod
    def _now():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def normalize(text: str) -> str:
        text = re.sub(r"\s+", " ", str(text or "").strip().lower())
        return text

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", LearningStore.normalize(text)))

    def begin_interaction(self, user_text: str) -> int:
        with self._lock, self._connect() as db:
            cur = db.execute(
                "INSERT INTO interactions(created_at,user_text) VALUES(?,?)",
                (self._now(), str(user_text)),
            )
            interaction_id = int(cur.lastrowid)
            self._starts[interaction_id] = time.perf_counter()
            return interaction_id

    def finish_interaction(self, interaction_id: Optional[int], source: str, intent: str,
                           output_text: str, success: bool = True) -> None:
        if not interaction_id:
            return
        started = self._starts.pop(int(interaction_id), None)
        duration_ms = (time.perf_counter() - started) * 1000.0 if started is not None else None
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE interactions SET source=?, intent=?, output_text=?, success=?, duration_ms=? WHERE id=?",
                (source, intent, str(output_text or ""), 1 if success else 0, duration_ms, interaction_id),
            )

    def feedback(self, interaction_id: Optional[int], score: int, note: str = "") -> bool:
        if not interaction_id:
            return False
        score = 1 if score > 0 else -1
        with self._lock, self._connect() as db:
            row = db.execute("SELECT id FROM interactions WHERE id=?", (interaction_id,)).fetchone()
            if not row:
                return False
            db.execute(
                "UPDATE interactions SET feedback=?, feedback_text=? WHERE id=?",
                (score, str(note or ""), interaction_id),
            )
        return True

    def learn_command(self, trigger: str, command: str) -> tuple[bool, str]:
        trigger_n = self.normalize(trigger)
        command = re.sub(r"\s+", " ", str(command or "").strip())
        if not trigger_n or not command:
            return False, "Both a trigger and command are required."
        if len(trigger_n) > 180 or len(command) > 800:
            return False, "That lesson is too long. Keep the trigger under 180 characters and the command under 800."
        now = self._now()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO lessons(created_at,trigger,command) VALUES(?,?,?) "
                "ON CONFLICT(trigger) DO UPDATE SET command=excluded.command, enabled=1",
                (now, trigger_n, command),
            )
        return True, f"Learned: when you say '{trigger}', I will treat it as '{command}'."

    def remove_lesson(self, trigger: str) -> bool:
        trigger_n = self.normalize(trigger)
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM lessons WHERE trigger=?", (trigger_n,))
            return cur.rowcount > 0

    def lookup_lesson(self, user_text: str):
        key = self.normalize(user_text)
        if not key:
            return None
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM lessons WHERE enabled=1 AND trigger=? LIMIT 1", (key,)
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE lessons SET uses=uses+1,last_used=? WHERE id=?",
                (self._now(), row["id"]),
            )
            return dict(row)

    def lesson_feedback(self, trigger: str, positive: bool):
        key = self.normalize(trigger)
        with self._lock, self._connect() as db:
            field = "successes" if positive else "failures"
            db.execute(f"UPDATE lessons SET {field}={field}+1 WHERE trigger=?", (key,))

    def set_preference(self, key: str, value: str):
        key = self.normalize(key)
        value = str(value).strip()
        if not key or not value:
            return
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO preferences(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, self._now()),
            )

    def get_preferences(self) -> dict[str, str]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT key,value FROM preferences ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def relevant_lessons(self, query: str, limit: int = 6):
        q = self._tokens(query)
        if not q:
            return []
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT trigger,command,uses,successes,failures FROM lessons WHERE enabled=1"
            ).fetchall()
        scored = []
        for row in rows:
            tokens = self._tokens(row["trigger"])
            overlap = len(q & tokens)
            if overlap:
                score = overlap / max(1, len(tokens))
                score += min(0.15, row["successes"] * 0.01)
                scored.append((score, dict(row)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def observe(self, user_text: str, action_key: str, success: bool = True) -> list[dict[str, Any]]:
        """Store a successful deterministic behavior and discover repeated patterns.

        Returns newly created/updated candidate suggestions.
        """
        text = re.sub(r"\s+", " ", str(user_text or "").strip())
        action_key = re.sub(r"\s+", " ", str(action_key or "").strip())
        if not text or not action_key:
            return []
        now = self._now()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO observations(created_at,user_text,action_key,success) VALUES(?,?,?,?)",
                (now, text, action_key, 1 if success else 0),
            )
            if not success:
                return []
            rows = db.execute(
                "SELECT user_text,action_key FROM observations WHERE success=1 ORDER BY id DESC LIMIT 80"
            ).fetchall()

            candidates: list[dict[str, Any]] = []

            # Repeated transitions are strong evidence for a routine. We only
            # consider the last 80 successful actions, requiring at least three
            # occurrences of the same ordered pair.
            recent = list(reversed([dict(r) for r in rows]))
            pair_counter: Counter[tuple[str, str]] = Counter()
            pair_examples: dict[tuple[str, str], tuple[str, str]] = {}
            for i in range(len(recent) - 1):
                left, right = recent[i], recent[i + 1]
                pair = (left["action_key"], right["action_key"])
                if pair[0] != pair[1]:
                    pair_counter[pair] += 1
                    pair_examples.setdefault(pair, (left["user_text"], right["user_text"]))

            for (step1, step2), evidence in pair_counter.most_common(8):
                if evidence < 3:
                    continue
                ex1, ex2 = pair_examples[(step1, step2)]
                trigger = self._suggest_trigger(step1, step2)
                steps = [ex1, ex2]
                steps_json = json.dumps(steps, ensure_ascii=False, separators=(",", ":"))
                db.execute(
                    "INSERT INTO evolution_candidates(created_at,updated_at,candidate_type,proposed_trigger,steps_json,evidence_count,status) "
                    "VALUES(?,?,?,?,?,?,'pending') "
                    "ON CONFLICT(candidate_type,proposed_trigger,steps_json) DO UPDATE SET "
                    "updated_at=excluded.updated_at,evidence_count=excluded.evidence_count",
                    (now, now, "workflow", trigger, steps_json, evidence),
                )

            # Repeated natural-language variations for one deterministic action
            # are useful as alias candidates. We require three uses of the same
            # action with meaningfully different wording before proposing one.
            action_groups: dict[str, list[dict[str, str]]] = {}
            for row in recent:
                action_groups.setdefault(row["action_key"], []).append(row)
            for key, items in action_groups.items():
                unique_phrases = []
                seen = set()
                for item in items:
                    norm = self.normalize(item["user_text"])
                    if norm not in seen:
                        seen.add(norm)
                        unique_phrases.append(item["user_text"])
                if len(items) >= 3 and len(unique_phrases) >= 3 and len(key) <= 180:
                    trigger = self._suggest_alias_trigger(key)
                    # Preserve a real executable example rather than the internal
                    # action signature (which may look like "action:open steam").
                    representative = min(unique_phrases, key=lambda x: len(str(x)))
                    steps_json = json.dumps([representative], ensure_ascii=False, separators=(",", ":"))
                    db.execute(
                        "INSERT INTO evolution_candidates(created_at,updated_at,candidate_type,proposed_trigger,steps_json,evidence_count,status) "
                        "VALUES(?,?,?,?,?,?,'pending') "
                        "ON CONFLICT(candidate_type,proposed_trigger,steps_json) DO UPDATE SET "
                        "updated_at=excluded.updated_at,evidence_count=excluded.evidence_count",
                        (now, now, "alias", trigger, steps_json, len(items)),
                    )

            rows2 = db.execute(
                "SELECT * FROM evolution_candidates WHERE status='pending' ORDER BY evidence_count DESC, updated_at DESC LIMIT 10"
            ).fetchall()
            candidates = [dict(r) for r in rows2]
        return candidates

    @staticmethod
    def _suggest_trigger(step1: str, step2: str) -> str:
        def clean(s: str) -> str:
            s = re.sub(r"^(?:action|command):\s*", "", s, flags=re.I)
            s = re.sub(r"^(open|launch|start)\s+", "", s, flags=re.I)
            return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
        a, b = clean(step1), clean(step2)
        label = " ".join(x for x in (a, b) if x)
        return f"{label} routine"[:180] if label else "saved routine"

    @staticmethod
    def _suggest_alias_trigger(action_key: str) -> str:
        label = re.sub(r"^(open|launch|start)\s+", "", action_key, flags=re.I)
        label = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        return f"quick {label}"[:180] if label else "quick action"

    def pending_candidates(self, limit: int = 20):
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM evolution_candidates WHERE status='pending' "
                "ORDER BY evidence_count DESC, updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def candidate(self, candidate_id: int):
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM evolution_candidates WHERE id=?", (int(candidate_id),)).fetchone()
        return dict(row) if row else None

    def set_candidate_status(self, candidate_id: int, status: str) -> bool:
        status = str(status).strip().lower()
        if status not in {"pending", "approved", "rejected"}:
            return False
        with self._lock, self._connect() as db:
            cur = db.execute(
                "UPDATE evolution_candidates SET status=?,updated_at=? WHERE id=?",
                (status, self._now(), int(candidate_id)),
            )
            return cur.rowcount > 0

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            interactions = db.execute("SELECT COUNT(*) n FROM interactions").fetchone()["n"]
            lessons = db.execute("SELECT COUNT(*) n FROM lessons WHERE enabled=1").fetchone()["n"]
            feedback = db.execute("SELECT COUNT(*) n FROM interactions WHERE feedback IS NOT NULL").fetchone()["n"]
            positive = db.execute("SELECT COUNT(*) n FROM interactions WHERE feedback=1").fetchone()["n"]
            negative = db.execute("SELECT COUNT(*) n FROM interactions WHERE feedback=-1").fetchone()["n"]
            observations = db.execute("SELECT COUNT(*) n FROM observations WHERE success=1").fetchone()["n"]
            pending = db.execute("SELECT COUNT(*) n FROM evolution_candidates WHERE status='pending'").fetchone()["n"]
            approved = db.execute("SELECT COUNT(*) n FROM evolution_candidates WHERE status='approved'").fetchone()["n"]
        return {
            "interactions": interactions, "lessons": lessons, "feedback": feedback,
            "positive": positive, "negative": negative, "observations": observations,
            "pending": pending, "approved": approved,
        }

    def recent_lessons(self, limit: int = 25):
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT trigger,command,uses,successes,failures,created_at,last_used "
                "FROM lessons ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


    def objective_telemetry(self) -> dict[str, Any]:
        """Return a compact evidence set for self-improvement scoring."""
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT source,intent,success,feedback,duration_ms,user_text,output_text FROM interactions ORDER BY id DESC LIMIT 500").fetchall()
            lessons = int(db.execute("SELECT COUNT(*) n FROM lessons WHERE enabled=1").fetchone()["n"])
            approved_skills = int(db.execute("SELECT COUNT(*) n FROM evolution_candidates WHERE status='approved'").fetchone()["n"])
            observations = int(db.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"])
            observed_success = int(db.execute("SELECT COUNT(*) n FROM observations WHERE success=1").fetchone()["n"])
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            cpu_pct = float(proc.cpu_percent(interval=0.05))
        except Exception:
            rss_mb = 0.0
            cpu_pct = 0.0
        gpu_pct = 0.0
        try:
            import subprocess
            kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
            proc = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=2, **kwargs)
            if proc.returncode == 0 and proc.stdout.strip():
                gpu_pct = float(proc.stdout.splitlines()[0].strip())
        except Exception:
            pass
        ai_rows = [r for r in rows if str(r["source"] or "") == "ai"]
        feedback = [int(r["feedback"]) for r in ai_rows if r["feedback"] is not None]
        pc_rows = [r for r in rows if str(r["source"] or "") in {"pc", "action_result", "action_pending"}]
        pc_total = len(pc_rows)
        pc_ok = sum(1 for r in pc_rows if int(r["success"] or 0))
        coding = []
        for r in ai_rows:
            txt = (str(r["user_text"] or "") + " " + str(r["output_text"] or "")).lower()
            if re.search(r"\b(code|coding|python|javascript|debug|bug|function|class|script|program)\b", txt):
                coding.append(int(r["feedback"] or 0) if r["feedback"] is not None else int(r["success"] or 0))
        durations = [float(r["duration_ms"]) for r in rows if r["duration_ms"] is not None]
        input_chars = [len(str(r["user_text"] or "")) for r in rows]
        output_chars = [len(str(r["output_text"] or "")) for r in rows]
        avg_context_chars = (sum(input_chars) / len(input_chars)) if input_chars else 0.0
        avg_output_chars = (sum(output_chars) / len(output_chars)) if output_chars else 0.0
        # Context efficiency proxy: smaller average turn footprint is better, but
        # this is explicitly telemetry rather than a claim of token optimality.
        token_context_efficiency = max(0.0, min(100.0, 100.0 * 2500.0 / max(2500.0, avg_context_chars + avg_output_chars))) if rows else 0.0
        web_rows = [r for r in rows if str(r["intent"] or "").upper() in {"EXTERNAL_KNOWLEDGE", "AI"} and "web" in (str(r["output_text"] or "").lower())]
        web_success = sum(1 for r in web_rows if int(r["success"] or 0))
        return {
            "all_total": len(rows), "all_success": sum(1 for r in rows if int(r["success"] or 0)),
            "ai_feedback_total": len(feedback), "ai_feedback_positive": sum(1 for x in feedback if x > 0),
            "coding_samples": len(coding), "coding_success_rate": (sum(1 for x in coding if x > 0) / len(coding) * 100.0) if coding else 0.0,
            "pc_total": pc_total, "pc_success": pc_ok,
            "workflow_total": observations, "workflow_success": observed_success,
            "launch_total": pc_total, "launch_failures": sum(1 for r in pc_rows if not int(r["success"] or 0)),
            "web_attempts": len(web_rows), "web_success": web_success, "sources_fetched": 0,
            "response_durations_ms": durations[-200:], "rss_mb": rss_mb, "cpu_pct": cpu_pct, "gpu_pct": gpu_pct,
            "token_context_efficiency": token_context_efficiency, "avg_context_chars": avg_context_chars, "avg_output_chars": avg_output_chars,
            "lessons": lessons, "approved_skills": approved_skills, "habit_predictions": observations,
            "habit_prediction_success_rate": (observed_success / observations * 100.0) if observations else 0.0,
            "repeat_task_reduction": (approved_skills / max(1, observations) * 100.0) if observations else 0.0,
            "evolution_regressions": 0,
        }

    def build_context(self, query: str, max_lessons: int = 5) -> str:
        parts = []
        prefs = self.get_preferences()
        if prefs:
            parts.append("Known user preferences:\n" + "\n".join(f"- {k}: {v}" for k, v in list(prefs.items())[:12]))
        lessons = self.relevant_lessons(query, max_lessons)
        if lessons:
            parts.append(
                "Relevant learned command mappings (use only as context; system actions remain safety-gated):\n"
                + "\n".join(f"- '{x['trigger']}' -> '{x['command']}'" for x in lessons)
            )
        pending = self.pending_candidates(limit=3)
        if pending:
            parts.append(
                "The local learning engine has pending suggestions. Do not claim they are active until the user approves them."
            )
        return "\n\n".join(parts)
    def add_observer_candidate(self, trigger: str, steps: list[str], evidence: int, candidate_type: str = "screen_workflow") -> None:
        if not trigger or not steps:
            return
        payload=json.dumps([str(x) for x in steps],ensure_ascii=False,separators=(",",":")); now=self._now()
        with self._lock,self._connect() as db:
            row=db.execute("SELECT id,evidence_count FROM evolution_candidates WHERE candidate_type=? AND proposed_trigger=? AND steps_json=?",(candidate_type,trigger,payload)).fetchone()
            if row:
                db.execute("UPDATE evolution_candidates SET evidence_count=?,updated_at=? WHERE id=?",(max(int(row["evidence_count"]),int(evidence)),now,row["id"]))
            else:
                db.execute("INSERT INTO evolution_candidates(created_at,updated_at,candidate_type,proposed_trigger,steps_json,evidence_count,positive_count,negative_count,status) VALUES(?,?,?,?,?,?,?,?,?)",(now,now,candidate_type,trigger,payload,int(evidence),0,0,"pending"))

