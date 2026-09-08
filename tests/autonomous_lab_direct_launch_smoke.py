from pathlib import Path
import sys, tempfile, subprocess, time, json, os, signal

ROOT = Path(__file__).resolve().parents[1]
worker = ROOT / 'services' / 'autonomous_lab.py'
assert worker.exists(), worker
proc = subprocess.Popen([sys.executable, str(worker)], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, 'MYLOCALAI_LAB_SMOKE':'1'})
try:
    time.sleep(1.0)
    state_path = ROOT / 'data' / 'autonomous_lab.json'
    assert state_path.exists(), 'state file missing'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    assert state.get('installed') is True, state
    assert state.get('pid') == proc.pid, (state, proc.pid)
finally:
    if proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=5)
print('AUTONOMOUS_LAB_DIRECT_LAUNCH_SMOKE_OK')
