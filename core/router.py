from dataclasses import dataclass
import re

@dataclass
class Intent:
    name: str
    confidence: float
    requires_tool: bool = False
    requires_approval: bool = False
    requires_model: bool = False

class IntentRouter:
    """Fast deterministic router. Falls back to CONVERSATION."""

    def classify(self, text, adapter):
        raw = (text or "").strip()
        low = raw.lower()

        if not raw:
            return Intent("EMPTY", 1.0)

        if low in ("exit", "quit"):
            return Intent("EXIT", 1.0)

        if adapter.is_reset_command(raw):
            return Intent("RESET", 1.0)

        if adapter.matches_learning_command(raw):
            return Intent("LEARNING_OPERATION", 1.0, requires_tool=True)

        if adapter.matches_external_command(raw):
            return Intent("EXTERNAL_KNOWLEDGE", 1.0, requires_tool=True)

        if adapter.has_pending_action():
            return Intent("PENDING_ACTION", 1.0, requires_approval=True)

        if adapter.matches_memory_command(raw):
            return Intent("MEMORY_OPERATION", 1.0, requires_tool=True)

        if adapter.matches_file_command(raw):
            return Intent("FILE_OPERATION", 0.98, requires_tool=True)

        if adapter.matches_scan_command(raw):
            return Intent("SECURITY_SCAN", 0.98, requires_tool=True)

        if adapter.matches_action(raw):
            return Intent("SYSTEM_ACTION", 1.0, requires_tool=True, requires_approval=True)

        route = adapter.route_command(raw)
        if route:
            return Intent("PC_COMMAND", 0.98, requires_tool=True)

        if re.search(r"\b(plan|steps?|workflow|build a project|multi-step)\b", low):
            return Intent("PLANNING", 0.70, requires_model=True)

        return Intent("CONVERSATION", 0.80, requires_model=True)
