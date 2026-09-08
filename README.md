# MyLocalAI v13.1 — Fast Autonomous Lab

Performance-focused maintenance release.

## Faster behavior
- Normal chat uses smart live-web search instead of an unconditional network round-trip for casual/local messages.
- Live research fetches independent pages concurrently.
- Autonomous research searches independent topic queries concurrently.
- The Autonomous Lab starts without importing the full MyLocalAI engine/model stack.
- The heavyweight engine is loaded lazily only when an evolution cycle is due.
- Background screen sampling defaults to 8 seconds to reduce idle CPU overhead.

## Commands
- `start autonomous lab`
- `autonomous lab status`
- `internet on`
- `research autopilot on`
- `self evolve`

# MyLocalAI v12.5 — Measured Deep Self-Evolution

This release turns self-evolution into an evidence-driven engineering loop.

## Core loop
observe → diagnose → propose → edit real source → validate → benchmark → compare objectives → keep or reject → backup → record

## Measured objectives
- **Intelligence (30%)**: answer-feedback accuracy, research grounding, coding-task success.
- **Knowledge (20%)**: live research success and source retrieval/fetch success.
- **Tool use (20%)**: command success rate, failed launches, workflow completion.
- **Efficiency (15%)**: response latency, process CPU/GPU/RAM telemetry, context-footprint proxy.
- **Reliability (10%)**: successful interaction rate and regression count.
- **Personalization (5%)**: useful learned skills, repeated-task reduction, habit prediction success.

Telemetry metrics are explicitly described as evidence/proxies. Missing samples are provisional rather than silently treated as perfect.

## Deep code editing
Autonomous evolution may edit deep application source across the stack, including `gui.py`, `pc_ai_engine.py`, Core, services, models, and tools. The evolution controller, mission policy, safety boundary, and evaluator tests remain protected from autonomous rewriting.

## Acceptance rule
A candidate must pass real AST parsing, compile validation, smoke imports, and the capability benchmark. Autonomous application requires a non-regressing benchmark and a **positive measured objective gain**. Neutral candidates remain reviewable but are not silently applied.

## Proof of change
Applied proposals record:
- exact files changed
- unified source diff
- benchmark before/after
- weighted objective score before/after
- category-level deltas
- rollback backup

This lets you verify that MyLocalAI actually changed code and whether the change improved the system.

## Commands
```text
self evolution on
self evolution status
self evolve
evolution report
objective report
measure yourself
self rollback
```

## Test suite
```bat
python tests\deep_audit.py
python tests\evolution_benchmark.py
python tests\self_evolution_smoke.py
```


## Autonomous AI Lab

Run `Run MyLocalAI Autonomous Lab.bat` to keep research, screen-learning metadata, and self-evolution running after the GUI is closed. Use `Install Autonomous Lab On Login.bat` to start it automatically when this Windows user logs in. See `AUTONOMOUS_LAB.md`.
# Planning and tracking your work with GitHub Projects and Issues

## Abstractttt

Learn how GitHub Issues and project planning capabilities balance structure and flexibility for complex software development. Get hands-on with the redesigned GitHub Issues experience and other enhancements and discover practical techniques for leveraging markdown-based issue templates and custom sections that adapt to your team's unique workflows. Perfect for developers and DevOps practitioners seeking to streamline collaboration without sacrificing visibility or accountability.

## Background 

This lab was created to support Microsoft Build 2025. A version for general public use will be published in a future release.

## Requirements

Currently, this lab requires the environment provided at Microsoft Build 2025.  In the future is should be available for use any GitHub organization.

## License 

This project is licensed under the terms of the MIT open source license. Please refer to the [LICENSE](./LICENSE) for the full terms.

## Maintainers 

@chrisreddington @dmckinstry

## Support

Please refer to the SUPPORT.md file for details.
