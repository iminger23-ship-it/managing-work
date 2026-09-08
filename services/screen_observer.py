from __future__ import annotations
import ctypes
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('mylocalai.screen')
SENSITIVE_RE = re.compile(r'(password|passcode|bank|banking|credit|debit|card number|social security|ssn|1password|bitwarden|keepass|lastpass|authenticator|private key|seed phrase|wallet)', re.I)

class ScreenObserver:
    """Opt-in foreground-app habit observer; background mode stores metadata, not screenshots."""
    def __init__(self, data_dir, interval=5):
        self.data_dir=Path(data_dir); self.data_dir.mkdir(parents=True,exist_ok=True)
        self.path=self.data_dir/'screen_learning.db'; self.interval=float(interval); self.enabled=False; self.stop_event=threading.Event(); self.thread=None; self.last=None; self.exclusions=[]; self.lock=threading.RLock(); self.vision=None; self.read_screen=True; self.vision_interval=15; self.last_vision_at=0.0; self.last_ocr_at=0.0; self.ocr_interval=10; self._init()
    @contextmanager
    def _db(self):
        c=sqlite3.connect(self.path,timeout=10,check_same_thread=False)
        c.row_factory=sqlite3.Row
        try:
            yield c
        finally:
            c.close()
    def _init(self):
        with self.lock,self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY,started_at TEXT,ended_at TEXT,process TEXT,title TEXT,duration REAL)'); db.execute('CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,created_at TEXT,process TEXT,title TEXT,ocr_text TEXT,allowed INTEGER)'); db.commit()
    def _foreground(self):
        if os.name!='nt': return ('non-windows','Desktop')
        try:
            user32=ctypes.windll.user32; hwnd=user32.GetForegroundWindow()
            if not hwnd: return ('unknown','')
            buf=ctypes.create_unicode_buffer(512); user32.GetWindowTextW(hwnd,buf,512); title=buf.value.strip(); pid=ctypes.c_ulong(); user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid)); process='unknown'
            try:
                import psutil; process=psutil.Process(pid.value).name()
            except Exception: pass
            return process,title
        except Exception: return ('unknown','')
    def _excluded(self,process,title):
        hay=f'{process} {title}'; return any(re.search(p,hay,re.I) for p in self.exclusions) or bool(SENSITIVE_RE.search(hay))
    def _close_last(self,now):
        if not self.last:return
        dur=max(0,time.time()-self.last['started'])
        with self.lock,self._db() as db:
            db.execute('INSERT INTO activity(started_at,ended_at,process,title,duration) VALUES(?,?,?,?,?)',(self.last['started_at'],now,self.last['process'],self.last['title'],dur)); db.commit()
        self.last=None
    def observe_once(self,ocr=False):
        process,title=self._foreground(); blocked=self._excluded(process,title); now=datetime.now().isoformat(timespec='seconds')
        if self.last and (self.last['process']!=process or self.last['title']!=title): self._close_last(now)
        if blocked:
            # Close the preceding non-sensitive window before suppressing the
            # protected window; otherwise that activity interval is lost.
            self._close_last(now)
            self.last=None; return {'process':process,'title':title,'blocked':True}
        if not self.last: self.last={'process':process,'title':title,'started':time.time(),'started_at':now}
        ocr_text=''
        vision_text=''
        if ocr or (self.read_screen and (time.time()-self.last_ocr_at)>=self.ocr_interval):
            # Keep screen content in memory; only a compact analysis is stored.
            try: ocr_text=self._ocr_screen(); self.last_ocr_at=time.time()
            except Exception: ocr_text=''
            if any(re.search(p, ocr_text, re.I) for p in [r'password',r'credit card',r'social security',r'seed phrase',r'private key']):
                ocr_text=''
            now_ts=time.time()
            if self.vision and now_ts-self.last_vision_at >= self.vision_interval:
                try:
                    vision_text=self.vision.capture_and_analyze('''Analyze this desktop screenshot only for useful workflow/habit context. Identify the active app or visible workflow, UI state, and any obvious next step. Do not report secrets, credentials, personal data, messages, or financial information. Keep it under 300 words.''')[:2000]
                    self.last_vision_at=now_ts
                except Exception:
                    vision_text=''
        # Background learning stores only workflow metadata. OCR/vision output
        # can contain secrets or personal messages even when the window title
        # looks harmless, so never persist screen text.
        combined=''
        with self.lock,self._db() as db:
            db.execute('INSERT INTO observations(created_at,process,title,ocr_text,allowed) VALUES(?,?,?,?,?)',(now,process,title,combined,1)); db.commit()
        return {'process':process,'title':title,'blocked':False,'ocr':bool(ocr_text),'vision':bool(vision_text)}
    def _loop(self):
        while not self.stop_event.wait(self.interval):
            try:self.observe_once(False)
            except Exception:logger.exception('screen observation failed')
    def configure_vision(self, vision, interval=15):
        self.vision=vision
        try: self.vision_interval=max(5,float(interval))
        except Exception: self.vision_interval=15

    def start(self):
        if self.thread and self.thread.is_alive(): self.enabled=True; return
        self.enabled=True; self.stop_event.clear(); self.thread=threading.Thread(target=self._loop,daemon=True,name='ScreenObserver'); self.thread.start()
    def stop(self):
        self.enabled=False
        self.stop_event.set()
        self._close_last(datetime.now().isoformat(timespec='seconds'))
        thread=self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.interval + 1.0))
        if thread and not thread.is_alive():
            self.thread=None
    def status(self): return {'enabled':self.enabled,'interval':self.interval,'exclusions':list(self.exclusions),'last':self.last,'screen_reading':self.read_screen,'vision_available':bool(self.vision),'vision_interval':self.vision_interval}
    def _ocr_screen(self):
        try:
            import mss, pytesseract
            from PIL import Image
            with mss.mss() as sct:
                mon=sct.monitors[1]; shot=sct.grab(mon); img=Image.frombytes('RGB',shot.size,shot.rgb)
            return re.sub(r'\s+',' ',pytesseract.image_to_string(img,config='--psm 6')).strip()[:5000]
        except Exception: return ''
    def capture_preview(self):
        if not self.enabled: raise RuntimeError('Screen learning is disabled.')
        process,title=self._foreground()
        if self._excluded(process,title): raise RuntimeError('The current window is protected/excluded.')
        import mss
        out=self.data_dir/'screen_preview.png'
        with mss.mss() as sct: sct.shot(output=str(out),mon=1)
        return str(out)
    def recent_observations(self,limit=20):
        with self.lock,self._db() as db:
            rows=db.execute('SELECT * FROM observations WHERE allowed=1 ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def recent_activity(self,limit=100):
        with self.lock,self._db() as db: rows=db.execute('SELECT * FROM activity ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    def transitions(self,limit=12):
        rows=list(reversed(self.recent_activity(250))); prev=None; counts={}
        for r in rows:
            cur=r['process'].lower()
            if prev and prev!=cur: counts[(prev,cur)]=counts.get((prev,cur),0)+1
            prev=cur
        ranked=sorted(counts.items(),key=lambda x:x[1],reverse=True)
        return [{'from':a,'to':b,'count':c} for (a,b),c in ranked[:limit] if c>=2]
