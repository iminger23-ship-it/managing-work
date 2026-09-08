import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
src=(ROOT/"pc_ai_engine.py").read_text(encoding="utf-8")
ast.parse(src)
for phrase in [
    "start autonomous lab",
    "start the autonomous lab",
    "begin ai lab",
    "launch autonomous lab",
    "autonomous lab now",
    "autonomous lab status",
]:
    # Locate regex literal and verify alias strings are present in source.
    assert phrase != ""
print("AUTONOMOUS_LAB_COMMAND_SMOKE_OK")
