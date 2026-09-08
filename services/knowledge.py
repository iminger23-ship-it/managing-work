from __future__ import annotations
import re
import sqlite3
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from .internet import fetch

CHUNK = 2200

class KnowledgeStore:
    """Local searchable knowledge store for coding/AI/web material."""
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir); self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / 'knowledge.db'; self.lock = threading.RLock(); self._init()
    def _db(self):
        c = sqlite3.connect(self.path, timeout=10, check_same_thread=False); c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL'); return c
    def _init(self):
        with self.lock, self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sources(
                id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL,
                title TEXT,
                url TEXT UNIQUE,
                source_type TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources(id)
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
            """)
    def add_document(self, title, url, text, source_type='web'):
        text = re.sub(r'\n{3,}', '\n\n', str(text or '')).strip()
        if not text:
            return 0
        pieces = []
        buf = ''
        for para in [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]:
            if len(buf) + len(para) + 2 <= CHUNK:
                buf = (buf + '\n\n' + para).strip()
            else:
                if buf: pieces.append(buf)
                buf = para[:CHUNK]
        if buf: pieces.append(buf)
        now = datetime.now().isoformat(timespec='seconds')
        with self.lock, self._db() as db:
            db.execute('INSERT INTO sources(added_at,title,url,source_type) VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title, source_type=excluded.source_type', (now,title,url,source_type))
            sid = int(db.execute('SELECT id FROM sources WHERE url=?',(url,)).fetchone()['id'])
            db.execute('DELETE FROM chunks WHERE source_id=?',(sid,))
            db.executemany('INSERT INTO chunks(source_id,chunk_index,content) VALUES(?,?,?)',[(sid,i,p) for i,p in enumerate(pieces)])
        return len(pieces)
    def ingest_url(self, url, title=None):
        doc = fetch(url); title = title or doc['url']; count = self.add_document(title, doc['url'], doc['text'], 'web'); return title, doc['url'], count
    def ingest_github(self, repo, branch=''):
        repo = repo.strip().strip('/')
        if repo.lower().startswith('https://github.com/'):
            repo = repo.split('github.com/',1)[1]
        owner, name = (repo.split('/',1)+[''])[:2]
        if not owner or not name:
            raise ValueError('Use owner/repository, for example openai/openai-python')
        ref = ('?ref=' + urllib.parse.quote(branch)) if branch else ''
        api = f'https://api.github.com/repos/{owner}/{name}/contents{ref}'
        req = urllib.request.Request(api, headers={'User-Agent':'MyLocalAI/11.0','Accept':'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=12) as r: items = __import__('json').loads(r.read().decode('utf-8','replace'))
        if isinstance(items, dict): items=[items]
        wanted=[]
        for item in items[:100]:
            if item.get('type')!='file': continue
            path=str(item.get('path','')); low=path.lower()
            if low in {'readme.md','pyproject.toml','requirements.txt','setup.py'} or low.startswith(('docs/','examples/')) or low.endswith(('.py','.md','.txt')):
                wanted.append(item)
        total=0; files=0
        for item in wanted[:24]:
            url=item.get('download_url') or item.get('html_url')
            if not url: continue
            try:
                doc=fetch(url); text=doc['text']
            except Exception:
                continue
            if text:
                total += self.add_document(f'{owner}/{name}: {item.get("path","")}', f'github://{owner}/{name}/{item.get("path","")}', text, 'github'); files += 1
        return files,total
    def search(self, query, limit=8):
        toks=[t.lower() for t in re.findall(r'[a-zA-Z0-9_+#.-]+', str(query or '')) if len(t)>1]
        if not toks: return []
        with self.lock,self._db() as db:
            rows=db.execute('SELECT c.id,s.title,s.url,s.source_type,c.content FROM chunks c JOIN sources s ON s.id=c.source_id').fetchall()
        scored=[]
        for r in rows:
            txt=r['content'].lower(); score=sum(txt.count(t) for t in toks)
            if score: scored.append((score,dict(r)))
        scored.sort(key=lambda x:x[0],reverse=True)
        return [r for _,r in scored[:limit]]
    def context(self, query, limit=5):
        rows=self.search(query,limit)
        if not rows: return ''
        parts=['Local knowledge sources:']
        for i,r in enumerate(rows,1): parts.append(f'[{i}] {r["title"]} ({r["url"]})\n{r["content"][:1800]}')
        return '\n\n'.join(parts)
    def stats(self):
        with self.lock,self._db() as db:
            sources=int(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]); chunks=int(db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])
        return {'sources':sources,'chunks':chunks}
