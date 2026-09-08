# MyLocalAI 13.0 — Autonomous AI Lab

This release adds a separate long-running worker so MyLocalAI can continue researching, observing workflow metadata, and running self-evolution cycles when the GUI is closed.

## What it does

- Searches the public internet periodically for current AI, coding-agent, computer-use, evaluation, and efficiency research.
- Stores useful research in the local knowledge base.
- Observes foreground application/workflow metadata and in-memory OCR/vision through the existing screen observer.
- Generates evidence-based self-improvement candidates with the local model.
- Edits deep application code through the existing SelfEvolution pipeline.
- Validates candidates in an isolated copy, runs the capability benchmark, and keeps only candidates that pass the measured gate.
- Saves rollback backups for applied source changes.
- Logs autonomous activity to `logs\autonomous_lab.log` and state to `data\autonomous_lab.json`.

## Start while the GUI is closed

Run:

`Run MyLocalAI Autonomous Lab.bat`

For automatic start whenever you log into Windows, run:

`Install Autonomous Lab On Login.bat`

This creates a per-user Windows Scheduled Task. It does not require the GUI to remain open.

## Controls

The existing MyLocalAI commands still control the interactive system:

- `evolution report`
- `objective report`
- `self status`
- `self evolve`
- `research autopilot status`

For the background lab, inspect `data\autonomous_lab.json` and `logs\autonomous_lab.log`.

## Default cadence

- Research: every 2 hours
- Deep discovery query: about hourly while the lab is active
- Self-evolution: every 6 hours
- Screen observation: every 5 seconds

These intervals are bounded and can be changed in `data\autonomous_lab.json` under `config`.

## Important boundary

Internet content is reference material. MyLocalAI does not download and execute arbitrary programs discovered on the web. Self-generated source changes are limited by the existing evolution controller and validated before application.
