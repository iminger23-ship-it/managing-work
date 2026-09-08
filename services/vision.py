from __future__ import annotations
import base64
import json
import urllib.request
from io import BytesIO

class LocalVision:
    """Optional local screen understanding through Ollama plus OCR fallback."""
    def __init__(self, model='auto', ollama_url='http://localhost:11434'):
        self.model = model or 'auto'
        self.ollama_url = ollama_url.rstrip('/')

    def _models(self):
        try:
            req = urllib.request.Request(self.ollama_url + '/api/tags', headers={'Accept':'application/json'})
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read().decode('utf-8','replace'))
            return [str(x.get('name','')) for x in data.get('models',[]) if x.get('name')]
        except Exception:
            return []

    def pick_model(self):
        if self.model != 'auto':
            return self.model
        names = self._models()
        preferred = ('qwen3-vl', 'qwen2.5vl', 'gemma3', 'llava', 'minicpm-v', 'moondream')
        for needle in preferred:
            for name in names:
                if needle in name.lower():
                    return name
        return ''

    def analyze_image(self, png_bytes: bytes, prompt: str) -> str:
        model = self.pick_model()
        if not model:
            return ''
        payload = {
            'model': model,
            'messages': [{'role':'user','content':prompt,'images':[base64.b64encode(png_bytes).decode('ascii')]}],
            'stream': False,
            'options': {'temperature': 0.1},
        }
        req = urllib.request.Request(self.ollama_url + '/api/chat', data=json.dumps(payload).encode('utf-8'), headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8','replace'))
        return str(data.get('message',{}).get('content','')).strip()

    def capture_and_analyze(self, prompt: str) -> str:
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[1]
            shot = sct.grab(mon)
            # mss provides BGRA raw bytes; convert to a compact PNG without writing to disk.
            try:
                from PIL import Image
                img = Image.frombytes('RGB', shot.size, shot.rgb)
                buf = BytesIO(); img.save(buf, format='PNG', optimize=True)
                png = buf.getvalue()
            except Exception:
                return ''
        return self.analyze_image(png, prompt)
