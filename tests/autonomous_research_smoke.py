from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.research import ResearchManager

class FakeKnowledge:
    def ingest_url(self, url, title=None):
        return title or url, url, 1

rm = ResearchManager(tempfile.mkdtemp(), FakeKnowledge())
called = []
def fake_refresh(max_results_per_query=5):
    called.append(max_results_per_query)
    return {"searched": 5, "indexed_sources": 2, "chunks": 8}
rm.refresh = fake_refresh

rm.set_autopilot(True, interval_hours=0.5)
assert rm.autopilot_status()["autopilot_enabled"] is True
rm.stop_autopilot()
result = rm.autonomous_refresh(reason="smoke")
assert result["indexed_sources"] == 2
st = rm.autopilot_status()
assert st["last_auto_reason"] == "smoke"
assert st["last_auto_result"]["chunks"] == 8
print("AUTONOMOUS_RESEARCH_SMOKE_OK")
