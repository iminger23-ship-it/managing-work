from __future__ import annotations

import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core.mission import mission_context
from services.objectives import compare as compare_objectives


# Only these files may ever be auto-applied. Higher-risk files can still be
# proposed, reviewed, and manually applied later, but the autonomous loop will
# not rewrite them.
# Deep evolution: the model may revise application source across the whole
# stack. The control-plane files remain protected from autonomous edits so the
# safety/evolution policy cannot silently rewrite itself.
PROTECTED_AUTONOMY = {
    "services/self_evolution.py",
    "core/mission.py",
    "core/safety.py",
    "tests/evolution_benchmark.py",
    "tests/deep_audit.py",
    "tests/self_evolution_smoke.py",
}
PROPOSAL_EDITABLE = {
    "gui.py", "pc_ai_engine.py",
    "core/router.py", "core/context.py", "core/orchestrator.py", "core/response.py", "core/session.py", "core/learning.py",
    "services/internet.py", "services/live_research.py", "services/knowledge.py", "services/research.py", "services/screen_observer.py", "services/vision.py",
    "models/base.py", "models/ollama_backend.py", "tools/registry.py",
}
AUTO_EDITABLE = {p for p in PROPOSAL_EDITABLE if p not in PROTECTED_AUTONOMY}

MAX_FILE_BYTES = 80_000
MAX_PATCH_FILES = 4


class SelfEvolution:
    """Local, test-first self-improvement with rollback and bounded autonomy.

    The local model may propose source edits. Before an edit is applied, the
    candidate is built in an isolated temporary copy and all Python files are
    syntax-compiled. Autonomous mode is limited to AUTO_EDITABLE paths and
    always creates a rollback snapshot first.
    """

    def __init__(self, project_dir: str, data_dir: str, generate_fn: Optional[Callable[[str], str]] = None):
        self.project_dir = Path(project_dir).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state = self.data_dir / "self_evolution.json"
        self.proposals = self.data_dir / "self_evolution_proposals.jsonl"
        self.backup_root = self.data_dir / "evolution_backups"
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._generator = generate_fn

    def set_generator(self, generate_fn: Callable[[str], str]):
        self._generator = generate_fn

    def _write(self, path: Path, obj: dict[str, Any]):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state.read_text(encoding="utf-8"))
        except Exception:
            return {
                "mode": "autonomous",
                "last_run": None,
                "successful_runs": 0,
                "auto_runs": 0,
                "applied_patches": 0,
                "failed_validations": 0,
                "interaction_count": 0,
                "next_auto_at": 8,
                "last_apply": None,
                "last_rollback": None,
                "mission": mission_context(),
                "evolution_cycles": 0,
                "accepted_improvements": 0,
                "rejected_regressions": 0,
                "baseline_score": None,
                "best_score": None,
                "last_score": None,
                "last_score_delta": None,
                "best_objective_score": None,
            }

    def status(self):
        state = self._read_state()
        state.setdefault("mode", "autonomous")
        state.setdefault("mission", mission_context())
        state.setdefault("evolution_cycles", 0)
        state.setdefault("accepted_improvements", 0)
        state.setdefault("rejected_regressions", 0)
        state.setdefault("baseline_score", None)
        state.setdefault("best_score", None)
        state.setdefault("last_score", None)
        state.setdefault("last_score_delta", None)
        state.setdefault("best_objective_score", None)
        pending = list(self.proposals_for_review())
        state["pending_proposals"] = len(pending)
        state["editable_auto_paths"] = sorted(AUTO_EDITABLE)
        try:
            from services.objectives import snapshot
            # status() may be called without a learning store; runtime injection
            # happens from the engine, so leave a structural placeholder here.
            state["objective_framework"] = {"weights": {"intelligence": 30, "knowledge": 20, "tool_use": 20, "efficiency": 15, "reliability": 10, "personalization": 5}}
        except Exception:
            pass
        return state

    def set_mode(self, mode: str):
        mode = str(mode or "").strip().lower()
        aliases = {"on": "autonomous", "auto": "autonomous", "off": "guided", "safe": "guided"}
        mode = aliases.get(mode, mode)
        if mode not in {"guided", "autonomous"}:
            return False, "Mode must be guided or autonomous."
        state = self._read_state()
        state["mode"] = mode
        state["mission"] = mission_context()
        self._write(self.state, state)
        if mode == "autonomous":
            return True, "Autonomous evolution is ON. Only validated low-risk files can be auto-applied, with rollback snapshots." 
        return True, "Autonomous evolution is OFF. Improvements will be proposed and validated but not automatically applied."

    def note_interaction(self, success: bool):
        state = self._read_state()
        state["interaction_count"] = int(state.get("interaction_count", 0)) + 1
        due = bool(state.get("mode") == "autonomous" and success and state["interaction_count"] >= int(state.get("next_auto_at", 8)))
        if due:
            # Back off after each automatic cycle; the next cycle is further away
            # so the assistant does not spend its entire time rewriting itself.
            state["next_auto_at"] = state["interaction_count"] + 8
        self._write(self.state, state)
        return due

    def analyze(self, log_path: Optional[str] = None, screen_context: str = "", knowledge_context: str = ""):
        issues: list[str] = []
        slow: list[str] = []
        errors: list[str] = []
        p = Path(log_path) if log_path else None
        if p and p.exists():
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-1600:]
            for line in lines:
                if re.search(r"ERROR|Traceback|failed|exception", line, re.I):
                    errors.append(line[-600:])
                if re.search(r"slow|latency|timeout|took", line, re.I):
                    slow.append(line[-600:])
        if errors:
            issues.append("Recent failures detected in logs.")
        if slow:
            issues.append("Possible latency/timeout signals detected in logs.")
        if screen_context:
            issues.append("Recent screen/workflow context is available for efficiency analysis.")
        if knowledge_context:
            issues.append("Current AI/coding research context is available for design improvements.")
        return {
            "issues": issues[:12],
            "errors": errors[-30:],
            "slow": slow[-30:],
            "screen_context": screen_context[:6000],
            "knowledge_context": knowledge_context[:9000],
        }

    def create_proposal(self, diagnosis, model_output: str = "", applyable: Optional[list[str]] = None):
        proposal = {
            "id": int(time.time() * 1000),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": "source_or_config_improvement",
            "mode": "proposal",
            "diagnosis": diagnosis,
            "applyable_paths": applyable or [],
            "model_output": model_output[:12000],
            "policy": "Candidate source edits are validated in isolation. Autonomous mode may apply only AUTO_EDITABLE paths after validation and backup.",
        }
        with self.proposals.open("a", encoding="utf-8") as f:
            f.write(json.dumps(proposal, ensure_ascii=False) + "\n")
        return proposal

    def proposals_for_review(self):
        rows = {}
        if not self.proposals.exists():
            return []
        for line in self.proposals.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("changes") and row.get("id") is not None:
                rows[str(row["id"])] = row
            target = row.get("status_update_for")
            if target is not None and str(target) in rows:
                rows[str(target)]["status"] = row.get("status", rows[str(target)].get("status", "pending"))
                if row.get("apply_message"):
                    rows[str(target)]["apply_message"] = row["apply_message"]
        return [r for r in rows.values() if r.get("status", "pending") in {"validated_pending", "pending"}]

    def _source_snapshot(self, paths: list[str]) -> dict[str, str]:
        files = {}
        for rel in paths:
            p = self.project_dir / rel
            if p.is_file():
                try:
                    files[rel] = p.read_text(encoding="utf-8")
                except Exception:
                    continue
        return files

    def _prompt(self, diagnosis: dict[str, Any], sources: dict[str, str]) -> str:
        editable = sorted(PROPOSAL_EDITABLE)
        source_text = []
        for rel, content in sources.items():
            source_text.append(f"\n===== FILE: {rel} =====\n{content[:10000]}")
        return (
            "You are the self-improvement engineer for MyLocalAI, a local Windows personal AI assistant.\n"
            + mission_context() + "\n\n"
            "Your task is to make the AI system genuinely better across measurable objectives: answer accuracy, "
            "research quality, coding-task success, response time, CPU/GPU/RAM efficiency, token/context efficiency, "
            "command success, failed launches, workflow completion, useful learned skills, repeated-task reduction, "
            "and successful habit predictions. Use evidence from the diagnosis and propose the smallest change that "
            "can improve one or more metrics without breaking the others. Do not add unrelated features.\n\n"

            "STRICT RULES:\n"
            "- Return JSON only. No markdown and no prose outside JSON.\n"
            "- Deep code editing is allowed across the application stack, including gui.py and pc_ai_engine.py.\n"
            "- You may change ONLY these paths: " + ", ".join(editable) + "\n"
            "- Never add arbitrary shell execution, credential access, persistence, spyware, or destructive behavior.\n"
            "- Preserve existing public APIs unless the change is necessary and safe.\n"
            "- Prefer the smallest change that directly addresses evidence.\n"
            "- Full file contents are required in each change.\n"
            "- If there is not enough evidence for a worthwhile change, return an empty changes list.\n\n"
            "OUTPUT SCHEMA:\n"
            '{"summary":"...","changes":[{"path":"core/router.py","content":"FULL FILE CONTENT"}],"tests":["..."],"confidence":0.0}\n\n'
            f"DIAGNOSIS:\n{json.dumps(diagnosis, ensure_ascii=False, indent=2)[:16000]}\n"
            + "\n".join(source_text)
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract one JSON object from imperfect local-model output.

        Local models sometimes wrap JSON in markdown, add a short preamble, or
        emit additional text after the object. We first try strict parsing, then
        fenced JSON, then a brace-balanced scan so a JSON string containing
        braces does not cause us to truncate at the wrong location.
        """
        raw = str(text or "").strip()
        candidates = [raw]
        unfenced = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        unfenced = re.sub(r"\s*```$", "", unfenced).strip()
        if unfenced != raw:
            candidates.insert(0, unfenced)

        def parse(candidate: str):
            try:
                value = json.loads(candidate)
                return value if isinstance(value, dict) else None
            except (json.JSONDecodeError, TypeError, ValueError):
                return None

        for candidate in candidates:
            value = parse(candidate)
            if value is not None:
                return value

        # Brace-balanced extraction, respecting JSON string escaping.
        source = candidates[0] if candidates else raw
        start = source.find("{")
        while start >= 0:
            depth = 0
            in_string = False
            escaped = False
            for i in range(start, len(source)):
                ch = source[i]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == "\"":
                        in_string = False
                    continue
                if ch == "\"":
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        value = parse(source[start:i + 1])
                        if value is not None:
                            return value
                        break
            start = source.find("{", start + 1)
        raise ValueError("Model did not return a valid JSON evolution plan.")

    def _generate_plan(self, prompt: str) -> tuple[dict[str, Any], str]:
        """Generate a plan, with one automatic JSON-repair attempt."""
        output = self._generator(prompt)
        try:
            return self._extract_json(output), output
        except ValueError:
            repair_prompt = (
                "Your previous evolution response was not valid JSON. Repair it.\n"
                "Return ONLY one valid JSON object matching this schema exactly: "
                "{\"summary\":\"...\",\"changes\":[{\"path\":\"allowed/path.py\",\"content\":\"FULL FILE CONTENT\"}],"
                "\"tests\":[\"...\"],\"confidence\":0.0}\n"
                "Do not use markdown fences, comments outside JSON, or explanatory prose.\n"
                "Allowed paths are explicitly provided in the original task.\n"
                "Previous output follows:\n---\n" + str(output)[:24000] + "\n---"
            )
            repaired = self._generator(repair_prompt)
            return self._extract_json(repaired), repaired

    def _validate_changes(self, changes: list[dict[str, Any]]):
        if not changes or len(changes) > MAX_PATCH_FILES:
            return False, "No changes or too many files in one patch.", None
        clean: list[dict[str, str]] = []
        seen = set()
        for item in changes:
            path = str(item.get("path", "")).replace("\\", "/").lstrip("/")
            content = item.get("content")
            if path in seen or path not in PROPOSAL_EDITABLE:
                return False, f"Path not allowed: {path}", None
            seen.add(path)
            if not isinstance(content, str) or not content.strip():
                return False, f"Missing content for {path}", None
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                return False, f"Patch too large: {path}", None
            clean.append({"path": path, "content": content})
        with tempfile.TemporaryDirectory(prefix="mylocalai-evo-") as td:
            tmp_root = Path(td) / "project"
            shutil.copytree(self.project_dir, tmp_root, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "logs", "data", "evolution_backups", ".venv", "venv"))
            for item in clean:
                target = tmp_root / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["content"], encoding="utf-8")
            errors = []
            for path in tmp_root.rglob("*.py"):
                try:
                    ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
                except Exception as exc:
                    errors.append(f"{path.relative_to(tmp_root)}: {type(exc).__name__}: {exc}")
            if errors:
                return False, "Syntax validation failed: " + " | ".join(errors[:8]), None
            # Run compileall as a second independent parser/bytecode check.
            proc = subprocess.run([
                sys.executable, "-m", "compileall", "-q", str(tmp_root)
            ], capture_output=True, text=True, timeout=45)
            if proc.returncode != 0:
                return False, (proc.stderr or proc.stdout or "compileall failed")[-4000:], None
            smoke = subprocess.run([
                sys.executable, "-c",
                "import core.router, core.context, tools.registry, services.internet",
            ], cwd=str(tmp_root), capture_output=True, text=True, timeout=30)
            if smoke.returncode != 0:
                return False, (smoke.stderr or smoke.stdout or "smoke import failed")[-4000:], None
            return True, "Validated isolated candidate (AST, compileall, smoke imports).", clean

    def _backup(self, paths: list[str]) -> Path:
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time()*1000)%1000:03d}"
        root = self.backup_root / stamp
        root.mkdir(parents=True, exist_ok=True)
        manifest = {}
        for rel in paths:
            src = self.project_dir / rel
            if src.exists():
                dst = root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                manifest[rel] = True
            else:
                manifest[rel] = False
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return root

    def apply_changes(self, changes: list[dict[str, str]], autonomous: bool = False):
        paths = [item["path"] for item in changes]
        if autonomous and any(p not in AUTO_EDITABLE for p in paths):
            return False, "Autonomous mode refused a higher-risk file. Review it manually first."
        ok, message, clean = self._validate_changes(changes)
        if not ok:
            state = self._read_state(); state["failed_validations"] = int(state.get("failed_validations", 0)) + 1; self._write(self.state, state)
            return False, message
        backup = self._backup(paths)
        try:
            for item in clean:
                target = self.project_dir / item["path"]
                tmp = target.with_suffix(target.suffix + ".evo.tmp")
                tmp.write_text(item["content"], encoding="utf-8")
                tmp.replace(target)
        except Exception as exc:
            # Restore immediately so a multi-file apply cannot leave a half-patched tree.
            try:
                manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
                for rel, existed in manifest.items():
                    src = backup / rel
                    dst = self.project_dir / rel
                    if existed and src.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                    elif not existed and dst.exists():
                        dst.unlink()
            except Exception:
                pass
            return False, f"Apply failed and was rolled back: {type(exc).__name__}: {exc}. Backup: {backup.name}"
        state = self._read_state()
        state["last_apply"] = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "backup": str(backup),
            "paths": paths,
        }
        state["applied_patches"] = int(state.get("applied_patches", 0)) + 1
        state["successful_runs"] = int(state.get("successful_runs", 0)) + 1
        self._write(self.state, state)
        return True, f"Applied validated self-improvement to {len(paths)} file(s). Backup: {backup.name}"

    def rollback(self, backup_name: Optional[str] = None):
        choices = sorted([p for p in self.backup_root.iterdir() if p.is_dir()], key=lambda p: p.name)
        if not choices:
            return False, "No evolution backups exist."
        root = self.backup_root / backup_name if backup_name else choices[-1]
        if not root.exists() or not root.is_dir():
            return False, f"Backup not found: {backup_name}"
        manifest_path = root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return False, "Backup manifest is missing or invalid."
        restored = []
        for rel, existed in manifest.items():
            src = root / rel
            dst = self.project_dir / rel
            if existed and src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                restored.append(rel)
            elif not existed and dst.exists():
                try:
                    dst.unlink()
                    restored.append(rel + " (deleted)")
                except OSError:
                    pass
        state = self._read_state()
        state["last_rollback"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "backup": root.name, "paths": restored}
        self._write(self.state, state)
        return True, f"Rolled back {len(restored)} file(s) from {root.name}. Restart MyLocalAI to load restored source."

    def _build_diff(self, clean: list[dict[str, str]]) -> str:
        chunks = []
        for item in clean:
            rel = item["path"]
            old_path = self.project_dir / rel
            old = old_path.read_text(encoding="utf-8", errors="replace").splitlines() if old_path.exists() else []
            new = item["content"].splitlines()
            chunks.extend(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
        return "\n".join(chunks)[:50000]

    def _run_benchmark(self, root: Path) -> dict[str, Any]:
        """Run the dependency-light local capability benchmark in a project copy."""
        script = root / "tests" / "evolution_benchmark.py"
        if not script.exists():
            return {"score": 0, "max_score": 100, "error": "benchmark script missing"}
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=45,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            text = (proc.stdout or "").strip().splitlines()
            for line in reversed(text):
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and "score" in value:
                        value["returncode"] = proc.returncode
                        return value
                except Exception:
                    continue
            return {"score": 0, "max_score": 100, "error": (proc.stderr or proc.stdout or "benchmark failed")[-3000:]}
        except Exception as exc:
            return {"score": 0, "max_score": 100, "error": f"{type(exc).__name__}: {exc}"}

    def _benchmark_candidate(self, clean: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
        current = self._run_benchmark(self.project_dir)
        with tempfile.TemporaryDirectory(prefix="mylocalai-bench-") as td:
            root = Path(td) / "project"
            shutil.copytree(
                self.project_dir,
                root,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "logs", "data", "evolution_backups", ".venv", "venv"),
            )
            for item in clean:
                target = root / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["content"], encoding="utf-8")
            candidate = self._run_benchmark(root)
        return current, candidate

    @staticmethod
    def _objective_from_benchmark(result: dict[str, Any]) -> dict[str, Any]:
        scores = result.get("objective_scores") or {}
        fallback = float(result.get("score", 0.0)) / max(1.0, float(result.get("max_score", 100.0))) * 100.0
        categories = {}
        for name in ("intelligence", "knowledge", "tool_use", "efficiency", "reliability", "personalization"):
            value = float(scores.get(name, fallback))
            categories[name] = {"score": value, "confidence": "high", "metrics": {"benchmark_score": round(value, 2)}}
        weights = {"intelligence": 30.0, "knowledge": 20.0, "tool_use": 20.0, "efficiency": 15.0, "reliability": 10.0, "personalization": 5.0}
        composite = sum(categories[k]["score"] * weights[k] for k in weights) / 100.0
        return {"composite_score": round(composite, 2), "coverage": 100.0, "weights": weights, "categories": categories}

    def evolve(self, diagnosis: dict[str, Any], autonomous: bool = False):
        with self._lock:
            if not self._generator:
                return {"ok": False, "status": "no-generator", "message": "Local model generator is not connected."}
            sources = self._source_snapshot(sorted(PROPOSAL_EDITABLE))
            prompt = self._prompt(diagnosis, sources)
            try:
                plan, output = self._generate_plan(prompt)
            except Exception as exc:
                proposal = self.create_proposal(diagnosis, f"generation failed: {type(exc).__name__}: {exc}")
                return {"ok": False, "status": "generation-failed", "message": str(exc), "proposal": proposal}
            changes = plan.get("changes") if isinstance(plan.get("changes"), list) else []
            summary = str(plan.get("summary", "No summary provided."))[:1200]
            if not changes:
                proposal = self.create_proposal(diagnosis, output, [])
                proposal["status"] = "empty"
                return {"ok": True, "status": "no-change", "message": summary, "proposal": proposal}
            valid, reason, clean = self._validate_changes(changes)
            if not valid:
                proposal = self.create_proposal(diagnosis, output, [])
                proposal["status"] = "invalid"
                with self.proposals.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"status_update_for": proposal.get("id"), "status": "invalid", "reason": reason}) + "\n")
                return {"ok": False, "status": "validation-failed", "message": reason, "summary": summary}
            applyable = [c["path"] for c in clean if c["path"] in AUTO_EDITABLE]
            baseline, candidate_score = self._benchmark_candidate(clean)
            delta = int(candidate_score.get("score", 0)) - int(baseline.get("score", 0))
            try:
                objective_before = self._objective_from_benchmark(baseline)
                objective_after = self._objective_from_benchmark(candidate_score)
                objective_compare = compare_objectives(objective_before, objective_after)
            except Exception:
                objective_compare = {"before": 0.0, "after": 0.0, "delta": 0.0, "percent": 0.0, "category_delta": {}}
            state = self._read_state()
            state["evolution_cycles"] = int(state.get("evolution_cycles", 0)) + 1
            state["baseline_score"] = baseline.get("score")
            state["last_score"] = candidate_score.get("score")
            state["last_score_delta"] = delta
            state["best_score"] = max(int(state.get("best_score") or 0), int(candidate_score.get("score") or 0))
            state["best_objective_score"] = max(float(state.get("best_objective_score") or 0.0), float(objective_compare.get("after", 0.0)))
            state["last_objective_comparison"] = objective_compare
            self._write(self.state, state)
            objective_gain = float(objective_compare.get("delta", 0.0))
            benchmark_regressed = delta < 0
            if autonomous and (benchmark_regressed or objective_gain <= 0.0):
                if benchmark_regressed:
                    state["rejected_regressions"] = int(state.get("rejected_regressions", 0)) + 1
                self._write(self.state, state)
                proposal = self.create_proposal(diagnosis, output, applyable)
                proposal["status"] = "rejected_regression" if benchmark_regressed else "rejected_no_measured_gain"
                proposal["benchmark"] = {"baseline": baseline, "candidate": candidate_score, "delta": delta}
                proposal["objective_comparison"] = objective_compare
                with self.proposals.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": proposal["id"], "status": proposal["status"], "benchmark": proposal["benchmark"], "objective_comparison": objective_compare}, ensure_ascii=False) + "\n")
                reason = (
                    f"Candidate regressed the capability benchmark ({baseline.get('score', 0)} -> {candidate_score.get('score', 0)})."
                    if benchmark_regressed else
                    f"Candidate did not produce a measurable objective gain (delta {objective_gain:+.2f})."
                )
                return {"ok": True, "status": proposal["status"], "message": reason + " Not applied.", "proposal": proposal, "benchmark": proposal["benchmark"], "objective_comparison": objective_compare}
            proposal = {
                "id": int(time.time() * 1000),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "type": "source_patch",
                "mode": "autonomous" if autonomous else "guided",
                "summary": summary,
                "diagnosis": diagnosis,
                "changes": clean,
                "tests": plan.get("tests") if isinstance(plan.get("tests"), list) else [],
                "confidence": plan.get("confidence"),
                "validated": True,
                "status": "validated_pending",
                "applyable_paths": applyable,
                "benchmark": {"baseline": baseline, "candidate": candidate_score, "delta": delta},
                "objective_comparison": objective_compare,
                "diff": self._build_diff(clean),
            }
            with self.proposals.open("a", encoding="utf-8") as f:
                f.write(json.dumps(proposal, ensure_ascii=False) + "\n")
            if autonomous and all(c["path"] in AUTO_EDITABLE for c in clean):
                ok, message = self.apply_changes(clean, autonomous=True)
                proposal["status"] = "applied" if ok else "apply_failed"
                proposal["apply_message"] = message
                with self.proposals.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"status_update_for": proposal["id"], "status": proposal["status"], "apply_message": message}, ensure_ascii=False) + "\n")
                if ok:
                    state = self._read_state()
                    state["accepted_improvements"] = int(state.get("accepted_improvements", 0)) + 1
                    self._write(self.state, state)
                return {"ok": ok, "status": proposal["status"], "message": message, "summary": summary, "proposal": proposal}
            return {"ok": True, "status": "validated_pending", "message": "Validated improvement waiting for review.", "summary": summary, "proposal": proposal}

    def apply_proposal_id(self, proposal_id: str):
        target = str(proposal_id).strip()
        if not target.isdigit():
            return False, "Proposal id must be numeric."
        found = None
        if self.proposals.exists():
            for line in self.proposals.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("id")) == target and row.get("changes"):
                    found = row
        if not found:
            return False, "Proposal not found."
        if found.get("status") in {"applied", "apply_failed"}:
            return False, f"Proposal {target} is already {found.get('status')}."
        ok, message = self.apply_changes(found["changes"], autonomous=False)
        found["status"] = "applied" if ok else "apply_failed"
        found["apply_message"] = message
        with self.proposals.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"status_update_for": found["id"], "status": found["status"], "apply_message": message}, ensure_ascii=False) + "\n")
        return ok, message

    def apply_tuning(self, settings: dict):
        # Preserve the older safe-config API for compatibility.
        allowed = {"screen_interval", "screen_ocr_interval", "vision_model", "research_interval_hours"}
        changed = {k: settings[k] for k in allowed if k in settings}
        state = self._read_state()
        state.update({"last_run": time.strftime("%Y-%m-%dT%H:%M:%S"), "last_tuning": changed, "successful_runs": int(state.get("successful_runs", 0)) + 1})
        self._write(self.state, state)
        return changed
