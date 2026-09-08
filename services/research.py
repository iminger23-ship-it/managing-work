from __future__ import annotations
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from .internet import search

DEFAULT_QUERIES = [
    'latest AI research agents coding computer use reasoning model efficiency 2026',
    'latest AI coding agents software engineering research 2026',
    'latest multimodal computer use AI research 2026',
    'latest AI self improvement agent research evaluation 2026',
    'latest local AI inference efficiency research 2026',
]
DEFAULT_SOURCES = [
    {'title': 'OpenAI GPT-5.6 builder guide', 'url': 'https://openai.com/index/builders-guide-to-gpt-5-6/', 'date': '2026-08-13', 'topic': 'coding/agents/efficiency'},
    {'title': 'OpenAI GPT-5.6 in Kiro', 'url': 'https://openai.com/index/gpt-5-6-in-kiro/', 'date': '2026-08-24', 'topic': 'coding/agents'},
    {'title': 'Anthropic multiagent systems', 'url': 'https://www.anthropic.com/research/multiagent-systems', 'date': '2026-08-13', 'topic': 'agents'},
    {'title': 'Anthropic automated researchers', 'url': 'https://www.anthropic.com/research/automated-researchers-mitigate-alignment-failures', 'date': '2026-08-28', 'topic': 'self-improvement/evaluation'},
    {'title': 'Anthropic CHIVE', 'url': 'https://alignment.anthropic.com/2026/chive/', 'date': '2026-08-21', 'topic': 'interpretability/evaluation'},
    {'title': 'Google DeepMind August 2026 research', 'url': 'https://deepmind.google/', 'date': '2026-08-01', 'topic': 'multimodal/agents'},
]

class ResearchManager:
    def __init__(self, data_dir: str, knowledge_store):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge = knowledge_store
        self.state_path = self.data_dir / 'research_state.json'
        self.source_path = self.data_dir / 'research_sources.json'
        self._auto_stop = threading.Event()
        self._auto_thread = None
        self._auto_lock = threading.RLock()
        if not self.source_path.exists():
            self.source_path.write_text(json.dumps(DEFAULT_SOURCES, indent=2), encoding='utf-8')

    def _save_state(self, payload):
        try:
            tmp = self.state_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
            tmp.replace(self.state_path)
        except OSError:
            pass


    def _read_state(self):
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _merge_state(self, **updates):
        data = self._read_state()
        data.update(updates)
        self._save_state(data)
        return data

    def autopilot_status(self):
        data = self._read_state()
        data.setdefault('autopilot_enabled', True)
        data.setdefault('autopilot_interval_hours', 4)
        data['running'] = bool(self._auto_thread and self._auto_thread.is_alive())
        data['scope'] = 'live internet research; AI/coding/agent discovery prioritized'
        return data

    def set_autopilot(self, enabled: bool, interval_hours: float | None = None):
        enabled = bool(enabled)
        interval = float(interval_hours if interval_hours is not None else self._read_state().get('autopilot_interval_hours', 4))
        interval = max(0.5, min(interval, 168.0))
        self._merge_state(autopilot_enabled=enabled, autopilot_interval_hours=interval)
        if enabled:
            self.start_autopilot()
        else:
            self.stop_autopilot()
        return self.autopilot_status()

    def autonomous_refresh(self, reason='scheduled'):
        result = self.refresh(max_results_per_query=4)
        now = datetime.now().isoformat(timespec='seconds')
        interval = float(self._read_state().get('autopilot_interval_hours', 4) or 4)
        next_time = datetime.fromtimestamp(time.time() + interval * 3600).isoformat(timespec='seconds')
        self._merge_state(
            last_auto_refresh=now,
            next_auto_refresh=next_time,
            last_auto_reason=str(reason),
            last_auto_result=result,
        )
        return result

    def _autopilot_loop(self):
        # Delay briefly so application startup stays responsive, then refresh
        # immediately once and continue on a bounded interval.
        if self._auto_stop.wait(20.0):
            return
        while not self._auto_stop.is_set():
            try:
                state = self._read_state()
                if bool(state.get('autopilot_enabled', True)):
                    self.autonomous_refresh(reason='scheduled')
            except Exception:
                # Research is background enrichment; never crash the GUI.
                pass
            interval = float(self._read_state().get('autopilot_interval_hours', 4) or 4)
            interval = max(0.5, min(interval, 168.0))
            if self._auto_stop.wait(interval * 3600):
                break

    def start_autopilot(self):
        with self._auto_lock:
            if self._auto_thread and self._auto_thread.is_alive():
                return False
            self._auto_stop.clear()
            self._auto_thread = threading.Thread(target=self._autopilot_loop, daemon=True, name='MyLocalAI-Research')
            self._auto_thread.start()
            return True

    def stop_autopilot(self):
        with self._auto_lock:
            self._auto_stop.set()
            self._auto_thread = None
            return True

    def seed_curated_sources(self, enabled=True):
        if not enabled:
            return 0
        added = 0
        for item in DEFAULT_SOURCES:
            try:
                title, url, count = self.knowledge.ingest_url(item['url'], item['title'])
                if count:
                    added += 1
            except Exception:
                continue
        self._save_state({'last_refresh': datetime.now().isoformat(timespec='seconds'), 'seeded_sources': added})
        return added

    def refresh(self, max_results_per_query=5):
        found = []
        seen = set()
        queries = list(DEFAULT_QUERIES)

        def run_query(q):
            try:
                return search(q, max_results_per_query)
            except Exception:
                return []

        # Search independent research topics concurrently. This removes the
        # serial network wait across the curated AI/coding topic list.
        with ThreadPoolExecutor(max_workers=min(6, len(queries)), thread_name_prefix='ResearchSearch') as pool:
            future_map = {pool.submit(run_query, q): q for q in queries}
            results_by_query = {}
            for fut in as_completed(future_map):
                results_by_query[future_map[fut]] = fut.result()

        for q in queries:
            for item in results_by_query.get(q, []):
                url = item.get('url','')
                if not url or url in seen:
                    continue
                seen.add(url)
                found.append(item)

        indexed = 0
        chunks = 0
        selected = found[:30]

        def ingest(item):
            try:
                return self.knowledge.ingest_url(item['url'], item.get('title') or item['url'])
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=min(6, max(1, len(selected))), thread_name_prefix='ResearchFetch') as pool:
            futures = [pool.submit(ingest, item) for item in selected]
            for fut in futures:
                result = fut.result()
                if result and result[2]:
                    indexed += 1
                    chunks += result[2]

        self._save_state({'last_refresh': datetime.now().isoformat(timespec='seconds'), 'indexed_sources': indexed, 'chunks': chunks})
        return {'searched': len(found), 'indexed_sources': indexed, 'chunks': chunks}

    def status(self):
        data = {}
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
        except Exception:
            pass
        data['scope'] = 'AI research through August 2026'
        return data
