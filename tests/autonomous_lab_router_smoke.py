import ast, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (ROOT / 'pc_ai_engine.py').read_text(encoding='utf-8')
ast.parse(SRC)

pattern = re.compile(r'^(?:(?:autonomous\s+lab|ai\s+lab)\s+(on|off|status|now)|(?:start|begin|launch|run)\s+(?:the\s+)?(?:autonomous\s+lab|ai\s+lab))$', re.I)
examples = [
    'start autonomous lab',
    'start the autonomous lab',
    'begin autonomous lab',
    'launch autonomous lab',
    'run autonomous lab',
    'autonomous lab now',
    'ai lab now',
]
assert all(pattern.match(x) for x in examples), examples
print('AUTONOMOUS_LAB_ROUTER_SMOKE_OK')
