# MyLocalAI v13.0.3 — Autonomous Lab Status/Startup Fix

## Fixed
- Autonomous Lab status now reports installed=True when the worker and launcher files exist, even before first run.
- Status reports whether the recorded worker PID is currently alive.
- `start autonomous lab` enables the lab, records the child PID, and starts the worker directly.
- The worker writes a running state at startup and a stopped state on shutdown.
- Avoids false “not installed” state caused by an absent state file.

## Checks
- Python compilation: passed
- Autonomous lab router smoke: passed
