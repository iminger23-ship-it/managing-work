# MyLocalAI Self-Evolution

## Prime directive
Continuously make MyLocalAI smarter, more capable, more reliable, more efficient, and more useful by learning from real evidence, current research, user feedback, screen/workflow patterns, and measured outcomes.

## Measured objectives
The evolution system uses a weighted score instead of letting the model decide what “better” means.

- **Intelligence (30%)** — answer-feedback accuracy, research grounding, coding-task success
- **Knowledge (20%)** — live research success and source retrieval quality
- **Tool use (20%)** — command success, launch failures, workflow completion
- **Efficiency (15%)** — measured response time, process CPU/GPU/RAM, and context-footprint proxy
- **Reliability (10%)** — successful interactions and regression count
- **Personalization (5%)** — useful learned skills, repeated-task reduction, habit prediction success

User-feedback and telemetry metrics are clearly labeled as evidence proxies; missing samples are provisional. The bundled candidate benchmark supplies a full-coverage regression gate.

## Deep code evolution
Autonomous evolution may edit deep application code across the stack, including `pc_ai_engine.py`, `gui.py`, Core, services, models, and tools. The control plane (`services/self_evolution.py`, `core/mission.py`, `core/safety.py`) and evaluator tests remain protected from autonomous rewriting so the optimizer cannot silently remove its own evaluator or safety boundary.

## Acceptance rule
A candidate must pass real syntax/compile/import validation, must not regress the capability benchmark, and must produce a positive measured objective gain for autonomous application. Neutral candidates can be reviewed manually; they are not silently applied.

## Evidence record
Every applied change stores its file list, benchmark before/after, objective score before/after, category deltas, confidence, and rollback backup.

## Commands
```text
prime directive
self status
self evolve
evolution report
self evolution on
self evolution off
self rollback
```
