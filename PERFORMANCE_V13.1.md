# v13.1 Performance Pass

## Changes
1. Autonomous Lab no longer imports `pc_ai_engine` during bootstrap. Research, knowledge, and screen observation use lightweight services directly.
2. Full engine/model stack is loaded lazily for evolution cycles only.
3. Live web page fetches run concurrently (up to 4 workers).
4. Autonomous research search and source ingestion run concurrently with bounded workers.
5. Automatic web lookup is now intent-aware: current/factual/AI questions still trigger live research, while casual/local-control messages avoid unnecessary network latency.
6. Background screen metadata sampling defaults to 8 seconds.

## Verification
- DEEP_AUDIT_OK
- AUTONOMOUS_LAB_ROUTER_SMOKE_OK
- SELF_EVOLUTION_SMOKE_OK
- AUTONOMOUS_RESEARCH_SMOKE_OK
- capability benchmark: 100/100
