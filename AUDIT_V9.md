# MyLocalAI v9 — Full Bug Audit

## Documentation cleanup
Historical README files were consolidated into one `README.md` to reduce clutter
and conflicting installation/version instructions.

## Static checks
- Python files found: 14
- Python files compiled successfully: 14
- Findings recorded: 18

## Targeted checks
- `re` import in GUI: FAIL
- Global `bind_all` removed from GUI: FAIL
- Selected microphone wiring: PASS
- Side-effect-free memory detector exists: FAIL
- `forget` separated from reset aliases: FAIL
- Direct `psutil.ZombieProcessError` reference absent: FAIL

## Findings
- Silent exception handler: gui.py:46
- Silent exception handler: gui.py:760
- Silent exception handler: gui.py:772
- Silent exception handler: gui.py:167
- Silent exception handler: pc_ai_engine.py:330
- Silent exception handler: pc_ai_engine.py:1520
- Silent exception handler: pc_ai_engine.py:1639
- Silent exception handler: pc_ai_engine.py:598
- Silent exception handler: pc_ai_engine.py:640
- Silent exception handler: pc_ai_engine.py:658
- Silent exception handler: pc_ai_engine.py:1397
- Targeted check failed: gui imports re
- Targeted check failed: no global bind_all
- Targeted check failed: memory detector exists
- Targeted check failed: zombie process attribute removed
- Targeted check failed: forget is not reset alias
- Review subprocess visibility: gui.py:43
- Review subprocess visibility: pc_ai_engine.py:114

## Important limitation
This is a source/static audit plus Python compilation. It cannot prove Windows
microphone, CUDA, Ollama, or Tkinter runtime behavior without executing the
application on the target PC.
