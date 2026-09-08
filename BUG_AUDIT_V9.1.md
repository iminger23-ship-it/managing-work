# MyLocalAI v9.1 — Complete Core Tools Audit

Audited the current Core/UI release after the v8.8.x and v9 stability fixes.

Key repairs included:
- Windows COM initialized per TTS/diagnostic worker thread.
- Browser/Steam application launch no longer depends only on PATH.
- GUI regex import restored for markdown cleanup.
- Existing hidden-subprocess policy retained.
- Existing deterministic action/approval architecture preserved.

The release is intended as a bug-fix build, not a feature expansion.
Runtime testing on the user's Windows machine is still required for microphone,
CUDA, Ollama and installed-app detection.
