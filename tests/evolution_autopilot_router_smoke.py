import ast, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
src=(ROOT/"pc_ai_engine.py").read_text(encoding="utf-8")
ast.parse(src)
pat=re.compile(r"^evolution\s+autopilot\s+(on|off|status|now)$", re.I)
examples=["evolution autopilot on","evolution autopilot off","evolution autopilot status","evolution autopilot now"]
assert all(pat.match(x) for x in examples), examples
assert "EVOLUTION_AUTOPILOT_RE" in src
print("EVOLUTION_AUTOPILOT_ROUTER_SMOKE_OK")
