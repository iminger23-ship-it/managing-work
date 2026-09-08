from contextlib import contextmanager
import json, logging, os, queue, re, threading, time
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
import psutil
import pc_ai_engine as engine

APP_DIR=os.path.dirname(os.path.abspath(__file__))
# Keep Hugging Face/Whisper cache inside the app and avoid Xet transport issues on Windows.
os.environ.setdefault('HF_HUB_DISABLE_XET','1')
os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT','60')
os.environ.setdefault('HF_HUB_ETAG_TIMEOUT','60')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
os.environ.setdefault('HF_HUB_ENABLE_HF_TRANSFER','0')
os.environ.setdefault('HF_HOME',os.path.join(APP_DIR,'models','hf_cache'))
VOICE_MODEL_DIR=os.path.join(APP_DIR,'models','whisper')
os.makedirs(VOICE_MODEL_DIR,exist_ok=True)
DATA_DIR=os.path.join(APP_DIR,'data'); LOG_DIR=os.path.join(APP_DIR,'logs')
os.makedirs(DATA_DIR,exist_ok=True); os.makedirs(LOG_DIR,exist_ok=True)
SETTINGS_FILE=os.path.join(DATA_DIR,'settings.json')
logging.basicConfig(filename=os.path.join(LOG_DIR,'mylocalai.log'),level=logging.INFO,format='%(asctime)s %(levelname)s %(threadName)s %(message)s')
logger=logging.getLogger('mylocalai')

@contextmanager
def com_thread_context():
    if os.name != 'nt':
        yield; return
    initialized=False
    try:
        import comtypes
        comtypes.CoInitialize(); initialized=True
    except Exception:
        logger.exception('COM initialization failed')
    try:
        yield
    finally:
        if initialized:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                logger.exception('COM cleanup failed')

DEFAULT={
 'theme':'Midnight','accent':'#6d8cff','background':'#0b1020','surface':'#131c31','text':'#edf2ff','muted':'#8d9ab5','font':'Segoe UI','font_size':10,'ui_scale':100,
 'voice_input':True,'speak_responses':True,'speech_rate':175,'speech_volume':1.0,'performance':'Balanced','voice_model':'base.en','voice_device':'auto','voice_compute':'auto','voice_mic_choice':'Default microphone','internet_enabled':False,'screen_learning':False,'screen_interval':5
}
THEMES={'Midnight':{'background':'#0b1020','surface':'#131c31','accent':'#6d8cff','text':'#edf2ff','muted':'#8d9ab5'},'OLED Black':{'background':'#050505','surface':'#101010','accent':'#38d6c8','text':'#f5f5f5','muted':'#9a9a9a'},'Cyber Purple':{'background':'#100a1d','surface':'#1b1230','accent':'#a78bfa','text':'#f4efff','muted':'#b4a9c8'},'Terminal Green':{'background':'#06100a','surface':'#0d1c12','accent':'#4ade80','text':'#e8ffe9','muted':'#91b596'}}

def load_settings():
 try:
  with open(SETTINGS_FILE,'r',encoding='utf8') as f: d=json.load(f)
 except Exception: d={}
 x=DEFAULT.copy(); x.update(d); return x

def save_settings(s):
 tmp=SETTINGS_FILE+'.tmp'
 with open(tmp,'w',encoding='utf8') as f: json.dump(s,f,indent=2)
 os.replace(tmp,SETTINGS_FILE)

def gpu_metrics():
 try:
  import subprocess
  kw={'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if os.name=='nt' else {}
  r=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,**kw)
  if r.returncode==0 and r.stdout.strip():
   a=[x.strip() for x in r.stdout.splitlines()[0].split(',')]; return {'util':float(a[0]),'temp':float(a[1]),'used':float(a[2]),'total':float(a[3])}
 except Exception: pass
 return None

class VoiceController:
 """Stable local voice subsystem.

 Audio capture, preprocessing, model loading and TTS run in worker threads so a
 voice failure never freezes or terminates the GUI.
 """
 def __init__(self,app):
  self.app=app; self.speaking=False; self.tts=None; self.listening=False
  self.listen_stop=threading.Event(); self.tts_stop=threading.Event()
  self.model=None; self.model_key=None; self.model_lock=threading.Lock(); self.state_lock=threading.Lock()
  self.backend_note='Not initialized'; self._tts_generation=0; self._last_voice_error=None
  self.debug_dir=os.path.join(DATA_DIR,'voice_debug'); os.makedirs(self.debug_dir,exist_ok=True)

 def check(self):
  missing=[]
  for label,mod in [('sounddevice/numpy','sounddevice'),('faster-whisper','faster_whisper'),('pyttsx3','pyttsx3')]:
   try: __import__(mod)
   except Exception: missing.append(label)
  return missing

 def microphones(self):
  try:
   import sounddevice as sd
   return [(i,str(d.get('name','Microphone'))) for i,d in enumerate(sd.query_devices()) if int(d.get('max_input_channels',0) or 0)>0]
  except Exception:
   logger.exception('Microphone enumeration failed'); return []

 def selected_device(self,choice):
  if choice in (None,'auto','Default microphone'): return None
  try: return int(str(choice).split(':',1)[0])
  except Exception: return None

 def _cuda_runtime(self):
  try:
   import ctranslate2
   probe=getattr(ctranslate2,'get_cuda_device_count',None)
   if not callable(probe): return False,'Installed CTranslate2 build has no CUDA device probe'
   n=int(probe() or 0)
   if n>0: return True,f'CTranslate2 detected {n} CUDA device(s)'
   return False,'CTranslate2 reports no usable CUDA devices'
  except Exception as e:
   return False,f'CUDA probe failed: {type(e).__name__}: {e}'

 def _backend(self):
  pref=str(self.app.settings.get('voice_device','auto')).lower()
  if pref=='cpu': self.backend_note='CPU selected'; return 'cpu','int8'
  ok,note=self._cuda_runtime(); self.backend_note=note
  if pref in ('auto','cuda') and ok: return 'cuda','float16'
  return 'cpu','int8'

 def _model_name(self): return str(self.app.settings.get('voice_model','base.en')).strip() or 'base.en'

 def _load_model(self):
  from faster_whisper import WhisperModel
  name=self._model_name(); device,compute=self._backend(); key=(name,device,compute)
  with self.model_lock:
   if self.model is not None and self.model_key==key: return self.model
   self.app.events.put(('voice_status',f'Loading Whisper model: {name} ({device}/{compute})…'))
   def create(dev,comp):
    return WhisperModel(name,device=dev,compute_type=comp,download_root=VOICE_MODEL_DIR)
   try:
    model=create(device,compute)
   except Exception as first_error:
    if device=='cuda':
     logger.exception('GPU Whisper load failed; CPU fallback engaged')
     self.app.events.put(('voice_detail',f'GPU STT unavailable ({type(first_error).__name__}); trying CPU/int8…'))
     try: model=create('cpu','int8'); key=(name,'cpu','int8'); self.backend_note='GPU failed; CPU fallback active'
     except Exception as cpu_error: raise RuntimeError(self._model_error(first_error,cpu_error)) from cpu_error
    else: raise RuntimeError(self._model_error(first_error)) from first_error
   self.model=model; self.model_key=key
   self.app.events.put(('voice_backend',f'Local STT ready: faster-whisper • {key[0]} • {key[1]}/{key[2]}'))
   return model

 def _model_error(self,first_error,cpu_error=None):
  msg=f'{type(first_error).__name__}: {first_error}'
  if cpu_error: msg+=f'; CPU fallback: {type(cpu_error).__name__}: {cpu_error}'
  low=msg.lower()
  if any(x in low for x in ('winerror 10038','winerror 10054','connection','readerror','socket')):
   msg+='\n\nWhisper model files are not fully cached and the download connection failed. Audio capture is still working. Retry model loading later; once cached, speech recognition runs locally.'
  elif 'cuda' in low or 'cublas' in low:
   msg+='\n\nCUDA voice acceleration is unavailable. CPU/int8 fallback was attempted automatically.'
  return 'Whisper model could not load: '+msg

 def warmup(self):
  def work():
   try: self._load_model(); self.app.events.put(('voice_status','Voice model ready — local STT online'))
   except Exception as e:
    logger.exception('Voice warmup failed'); self.app.events.put(('voice_error',f'Whisper setup failed ({type(e).__name__}): {e}\n\nMicrophone testing remains available. Check logs/mylocalai.log for details.'))
  threading.Thread(target=work,daemon=True,name='VoiceWarmup').start()

 def diagnostics(self):
  def work():
   lines=[]; missing=self.check(); lines.append('Python packages: '+('PASS' if not missing else 'MISSING '+', '.join(missing)))
   mics=self.microphones(); lines.append(f'Microphones: {len(mics)} detected')
   ok,note=self._cuda_runtime(); lines.append('GPU STT: '+('READY — '+note if ok else 'CPU FALLBACK — '+note))
   try:
    with com_thread_context():
     import pyttsx3; e=pyttsx3.init(); voices=e.getProperty('voices') or []; e.stop(); lines.append(f'TTS: PASS ({len(voices)} voice(s))')
   except Exception as e: lines.append(f'TTS: FAIL — {type(e).__name__}: {e}')
   lines.append('Model cache: '+VOICE_MODEL_DIR); lines.append('Audio debug: '+self.debug_dir)
   self.app.events.put(('voice_detail','\n'.join(lines))); self.app.events.put(('voice_status','Diagnostics complete'))
  threading.Thread(target=work,daemon=True,name='VoiceDiagnostics').start()

 def speak(self,text,force=False):
  if (not force and not self.app.settings.get('speak_responses')) or not str(text).strip(): return
  clean=str(text).strip(); clean=clean[:3500]+('…' if len(clean)>3500 else '')
  with self.state_lock:
   self._tts_generation+=1; generation=self._tts_generation; previous=self.tts; self.speaking=True; self.tts_stop.clear()
  try:
   if previous: previous.stop()
  except Exception: logger.exception('Previous TTS stop failed')
  def work():
   tts=None
   try:
    with com_thread_context():
     import pyttsx3
     self.app.events.put(('voice_status','Speaking AI response…')); tts=pyttsx3.init()
     with self.state_lock:
      if generation!=self._tts_generation:
       try: tts.stop()
       except Exception: pass
       return
      self.tts=tts
     tts.setProperty('rate',int(self.app.settings.get('speech_rate',175))); tts.setProperty('volume',float(self.app.settings.get('speech_volume',1.0)))
     tts.say(clean); tts.runAndWait()
   except Exception as e:
    logger.exception('TTS failed'); self.app.events.put(('voice_error','Local TTS failed: '+str(e)))
   finally:
    ready=False
    with self.state_lock:
     if generation==self._tts_generation: self.tts=None; self.speaking=False; ready=not self.listening
    if ready: self.app.events.put(('voice_status','Ready'))
  threading.Thread(target=work,daemon=True,name='VoiceTTS').start()

 def stop_speaking(self):
  with self.state_lock: self._tts_generation+=1; tts=self.tts; self.tts=None; self.speaking=False
  self.tts_stop.set()
  try:
   if tts: tts.stop()
  except Exception: logger.exception('TTS stop failed')
  self.app.events.put(('voice_status','Speech stopped'))

 def stop_listening(self): self.listen_stop.set(); self.app.events.put(('voice_status','Stopping microphone…'))
 def stop(self): self.stop_listening(); self.stop_speaking()

 def _device_info(self,device):
  import sounddevice as sd
  info=sd.query_devices(device,kind='input') if device is not None else sd.query_devices(kind='input')
  sr=int(float(info.get('default_samplerate') or 16000))
  channels=max(1,min(2,int(info.get('max_input_channels',1) or 1)))
  return info,sr,channels

 def _analyze_audio(self,a,sr,channels=None):
  import numpy as np
  x=np.asarray(a,dtype=np.float32)
  mono=np.mean(x,axis=1) if x.ndim>1 else x.reshape(-1)
  rms=float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
  peak=float(np.max(np.abs(mono))) if mono.size else 0.0
  return {'duration':float(len(mono)/sr) if sr else 0.0,'sample_rate':int(sr),'channels':int(channels or (x.shape[1] if x.ndim>1 else 1)),'rms':rms,'peak':peak,'samples':int(len(mono))}

 def _prepare_audio(self,a,sr):
  """Explicitly produce Whisper-friendly 16 kHz mono float32 without scipy."""
  import numpy as np
  x=np.asarray(a,dtype=np.float32)
  if x.ndim>1: x=np.mean(x,axis=1)
  x=x.reshape(-1)
  if not x.size: return x.astype(np.float32)
  x=x-float(np.mean(x))
  target=16000
  if int(sr)!=target and len(x)>1:
   n=max(1,int(round(len(x)*target/float(sr))))
   old=np.linspace(0.0,1.0,len(x),endpoint=False,dtype=np.float64)
   new=np.linspace(0.0,1.0,n,endpoint=False,dtype=np.float64)
   x=np.interp(new,old,x).astype(np.float32)
  peak=float(np.max(np.abs(x))) if x.size else 0.0
  # Normalize only usable speech; never turn digital silence into loud fake audio.
  if peak>1e-4: x=(x/peak*0.95).astype(np.float32)
  return np.clip(x,-1.0,1.0).astype(np.float32)

 def _save_debug_wav(self,a,sr):
  try:
   import wave, numpy as np
   x=np.asarray(a,dtype=np.float32)
   if x.ndim>1: x=np.mean(x,axis=1)
   pcm=(np.clip(x,-1,1)*32767).astype('<i2')
   path=os.path.join(self.debug_dir,'last_capture.wav')
   with wave.open(path,'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(sr)); w.writeframes(pcm.tobytes())
   return path
  except Exception:
   logger.exception('Could not save voice debug WAV'); return None

 def _record_fixed(self,device,duration=5.0):
  import sounddevice as sd
  info,sr,ch=self._device_info(device)
  try: sd.check_input_settings(device=device,channels=ch,samplerate=sr,dtype='float32')
  except Exception:
   ch=1; sd.check_input_settings(device=device,channels=1,samplerate=sr,dtype='float32')
  self.app.events.put(('voice_detail',f"Recording from {info['name']} • {sr} Hz • {ch} channel(s)"))
  data=sd.rec(int(sr*duration),samplerate=sr,channels=ch,dtype='float32',device=device); sd.wait()
  return data,sr,ch,info

 def _capture(self,device):
  import sounddevice as sd, numpy as np
  info,sr,ch=self._device_info(device)
  try: sd.check_input_settings(device=device,channels=ch,samplerate=sr,dtype='float32')
  except Exception: ch=1
  block=max(256,min(2048,int(sr/20))); chunks=[]; started=False; quiet=0.0; elapsed=0.0
  self.app.events.put(('voice_detail',f"Using microphone: {info['name']} • {sr} Hz • {ch} channel(s)")); self.listen_stop.clear(); self.app.events.put(('voice_status','Calibrating microphone… stay quiet briefly'))
  cal=sd.rec(int(sr*.5),samplerate=sr,channels=ch,dtype='float32',device=device); sd.wait()
  noise=float(np.sqrt(np.mean(np.square(cal)))) if cal.size else 0.0; threshold=max(.0025,noise*2.5)
  self.app.events.put(('voice_detail',f'Noise floor {noise:.4f} • speech threshold {threshold:.4f}')); self.app.events.put(('voice_status','Listening… speak now'))
  def callback(indata,frames,time_info,status):
   if status: logger.warning('Audio callback status: %s',status)
   chunks.append(indata.copy())
  with sd.InputStream(samplerate=sr,channels=ch,dtype='float32',device=device,blocksize=block,callback=callback):
   while elapsed<20 and not self.listen_stop.is_set():
    time.sleep(.05); elapsed+=.05
    if chunks:
     rms=float(np.sqrt(np.mean(np.square(chunks[-1]))))
     if rms>threshold: started=True; quiet=0.0
     elif started: quiet+=.05
     if started and quiet>1.4: break
  if not chunks or not started: return None,sr,ch
  return np.concatenate(chunks,axis=0),sr,ch

 def _transcribe(self,raw,sr,ch):
  stats=self._analyze_audio(raw,sr,ch)
  if stats['rms']<0.0015 or stats['peak']<0.004:
   return None,stats,None,'Signal is too quiet for reliable speech recognition.'
  audio=self._prepare_audio(raw,sr); path=self._save_debug_wav(raw,sr)
  model=self._load_model()
  segments,_=model.transcribe(audio,language='en',beam_size=3,vad_filter=True,condition_on_previous_text=False)
  text=' '.join(seg.text.strip() for seg in segments).strip()
  return text,stats,path,None

 def listen(self,device=None):
  with self.state_lock:
   if self.listening: self.app.events.put(('voice_status','Already listening…')); return
   self.listening=True
  missing=self.check()
  if missing:
   self.listening=False; self.app.events.put(('voice_error','Voice dependencies missing: '+', '.join(missing))); return
  def work():
   self.listen_stop.clear()
   try:
    self.app.events.put(('voice_status','Opening local microphone…')); raw,sr,ch=self._capture(device)
    if self.listen_stop.is_set(): self.app.events.put(('voice_status','Listening cancelled')); return
    if raw is None: self.app.events.put(('voice_warning','No speech detected — try speaking louder or closer to the microphone.')); return
    stats=self._analyze_audio(raw,sr,ch); self.app.events.put(('voice_status','Processing speech locally…')); self.app.events.put(('voice_detail',f"Captured {stats['duration']:.1f}s • RMS {stats['rms']:.4f} • Peak {stats['peak']:.4f} • {sr} Hz/{ch} ch → 16 kHz mono"))
    text,stats,path,warning=self._transcribe(raw,sr,ch)
    if warning: self.app.events.put(('voice_warning',warning)); return
    if text: self.app.events.put(('voice_transcript',text)); self.app.events.put(('voice_text',text)); self.app.events.put(('voice_status','Speech recognized'))
    else: self.app.events.put(('voice_warning','No intelligible speech was detected. Audio capture succeeded; try speaking clearly for longer.'))
   except Exception as e:
    logger.exception('Voice input failed'); self._last_voice_error=str(e); self.app.events.put(('voice_error',f'Voice pipeline failed ({type(e).__name__}): {e}'))
   finally:
    with self.state_lock: self.listening=False
    was_cancelled=self.listen_stop.is_set(); self.listen_stop.clear()
    if was_cancelled: self.app.events.put(('voice_status','Ready'))
  threading.Thread(target=work,daemon=True,name='LocalVoiceInput').start()

 def test_microphone(self,device=None):
  def work():
   try:
    self.app.events.put(('voice_status','Testing microphone — speak normally for 5 seconds…'))
    raw,sr,ch,info=self._record_fixed(device,5.0); stats=self._analyze_audio(raw,sr,ch); path=self._save_debug_wav(raw,sr)
    level='GOOD' if stats['rms']>=0.01 else ('LOW — increase microphone level' if stats['rms']>=0.0015 else 'TOO QUIET')
    msg=(f"MICROPHONE TEST\n{'─'*52}\nDevice: {info['name']}\nCapture: {stats['duration']:.1f} seconds • {sr} Hz • {ch} channel(s)\nSignal RMS: {stats['rms']:.5f}\nPeak: {stats['peak']:.5f}\nSignal status: {level}\n\nProcessing check:\nStereo/multi-channel → mono: PASS\n{sr} Hz → 16000 Hz: PASS\nFloat32 normalization: PASS\nDebug WAV: {path or 'unavailable'}")
    self.app.events.put(('voice_test',msg)); self.app.events.put(('voice_status','Microphone test complete'))
   except Exception as e: logger.exception('Microphone test failed'); self.app.events.put(('voice_error','Microphone test failed: '+str(e)))
  threading.Thread(target=work,daemon=True,name='VoiceMicTest').start()

 def test_recognition(self,device=None):
  def work():
   try:
    self.app.events.put(('voice_status','Recognition test — speak a short sentence for 6 seconds…'))
    raw,sr,ch,info=self._record_fixed(device,6.0); self.app.events.put(('voice_status','Preparing audio for Whisper…'))
    text,stats,path,warning=self._transcribe(raw,sr,ch)
    header=(f"VOICE RECOGNITION TEST\n{'─'*52}\nDevice: {info['name']}\nCapture: {stats['duration']:.1f}s • RMS {stats['rms']:.5f} • Peak {stats['peak']:.5f}\nPipeline: {sr} Hz/{ch} ch → 16000 Hz mono float32\nDebug WAV: {path or 'unavailable'}\n\n")
    if warning: self.app.events.put(('voice_test',header+'Result: '+warning)); self.app.events.put(('voice_warning',warning)); return
    if text:
     self.app.events.put(('voice_test',header+'Result: PASS\nRecognized text:\n'+text)); self.app.events.put(('voice_status','Recognition test passed'))
    else:
     self.app.events.put(('voice_test',header+'Result: No intelligible speech detected.')); self.app.events.put(('voice_warning','No intelligible speech detected — microphone capture succeeded.'))
   except Exception as e:
    logger.exception('Recognition test failed'); self.app.events.put(('voice_error',f'Recognition test failed ({type(e).__name__}): {e}'))
  threading.Thread(target=work,daemon=True,name='VoiceRecognitionTest').start()

class Card(tk.Frame):
 def __init__(self,parent,app,**kw):
  super().__init__(parent,bg=app.C['surface'],highlightbackground=app.C['border'],highlightthickness=1,bd=0,**kw); self.app=app


class ChatView(tk.Frame):
 def __init__(self,parent,app):
  super().__init__(parent,bg=app.C['bg'])
  self.app=app
  self.canvas=tk.Canvas(self,bg=app.C['bg'],highlightthickness=0,bd=0)
  self.scroll=tk.Scrollbar(self,orient='vertical',command=self.canvas.yview,
                           bg=app.C['surface2'],activebackground=app.C['border'],
                           troughcolor=app.C['bg'],highlightthickness=0,bd=0)
  self.canvas.configure(yscrollcommand=self.scroll.set)
  self.scroll.pack(side='right',fill='y')
  self.canvas.pack(side='left',fill='both',expand=True)
  self.inner=tk.Frame(self.canvas,bg=app.C['bg'])
  self.window=self.canvas.create_window((0,0),window=self.inner,anchor='nw')
  self.inner.bind('<Configure>',self._sync_region)
  self.canvas.bind('<Configure>',self._resize)
  # Bind wheel handlers directly on the canvas/inner frame (not bind_all):
  # bind_all installs a window-global binding that can steal scroll events
  # from other widgets (e.g. dropdowns elsewhere in the app) and, since it
  # was toggled on <Enter>/<Leave> of just the bare canvas background, it
  # actually dropped out the moment the pointer moved over a child label
  # or bubble (each child gets its own Enter/Leave). Binding per-widget
  # instead keeps scrolling working everywhere in the chat area and is
  # cleaned up automatically when a widget is destroyed.
  self._bind_wheel(self.canvas)
  self._bind_wheel(self.inner)
 def _sync_region(self,event=None):
  self.canvas.configure(scrollregion=self.canvas.bbox('all'))
 def _resize(self,event):
  self.canvas.itemconfigure(self.window,width=event.width)
  self._sync_region()
 def _bind_wheel(self,widget):
  widget.bind('<MouseWheel>',self._wheel)
  widget.bind('<Button-4>',self._wheel)
  widget.bind('<Button-5>',self._wheel)
 def _bind_wheel_recursive(self,widget):
  self._bind_wheel(widget)
  for child in widget.winfo_children():
   self._bind_wheel_recursive(child)
 def _wheel(self,event):
  if getattr(event,'delta',0):
   self.canvas.yview_scroll(int(-event.delta/120),'units')
  elif getattr(event,'num',None)==4:
   self.canvas.yview_scroll(-3,'units')
  elif getattr(event,'num',None)==5:
   self.canvas.yview_scroll(3,'units')
 def bottom(self):
  self.update_idletasks()
  self.canvas.yview_moveto(1.0)
 def add(self,who,text,kind='ai',feedback=False,feedback_id=None):
  row=tk.Frame(self.inner,bg=self.app.C['bg'])
  row.pack(fill='x',padx=18,pady=7)
  right = kind == 'you'
  bubble_bg = {
   'you': self.app.C['accent'],
   'ai': self.app.C['surface'],
   'pc': self.app.C['surface'],
   'error': '#3a1f2a',
   'system': self.app.C['surface2'],
   'action_pending': self.app.C['surface'],
   'action_result': self.app.C['surface'],
  }.get(kind,self.app.C['surface'])
  anchor = 'e' if right else 'w'
  side = 'right' if right else 'left'
  bubble=tk.Frame(row,bg=bubble_bg,highlightbackground=self.app.C['border'],
                  highlightthickness=(0 if kind=='you' else 1),bd=0)
  bubble.pack(side=side,anchor=anchor,padx=(80 if right else 0,0 if right else 80))
  if kind == 'pc':
   self._pc_card(bubble,text)
  elif kind == 'action_pending':
   self._action_card(bubble,text)
  else:
   self._text_bubble(bubble,who,text,kind)
   if feedback and kind == 'ai':
    actions=tk.Frame(bubble,bg=bubble['bg']); actions.pack(anchor='w',padx=14,pady=(0,10))
    tk.Button(actions,text='👍',command=lambda:self.app.rate_response(feedback_id,1),bg=bubble['bg'],fg=self.app.C['muted'],bd=0).pack(side='left',padx=(0,5))
    tk.Button(actions,text='👎',command=lambda:self.app.rate_response(feedback_id,-1),bg=bubble['bg'],fg=self.app.C['muted'],bd=0).pack(side='left')
  self._bind_wheel_recursive(row)
  self.bottom()
 def _text_bubble(self,bubble,who,text,kind):
  colors={'you':'#ffffff','ai':self.app.C['teal'],'error':self.app.C['red'],
          'system':self.app.C['muted'],'action_result':self.app.C['green']}
  title=who.upper()
  tk.Label(bubble,text=title,bg=bubble['bg'],fg=colors.get(kind,self.app.C['text']),
           font=(self.app.font,max(8,self.app.fs-1),'bold')).pack(anchor='w',padx=14,pady=(10,2))
  clean=self._clean_markdown(text)
  tk.Label(bubble,text=clean,bg=bubble['bg'],fg=self.app.C['text'],
           justify='left',anchor='w',wraplength=760,
           font=(self.app.font,self.app.fs)).pack(anchor='w',padx=14,pady=(0,11))
 def _pc_card(self,bubble,text):
  lines=[x.strip() for x in text.splitlines() if x.strip() and not re.fullmatch(r'[=\-─]{8,}',x.strip())]
  title='PC STATUS'
  pairs=[]; other=[]
  for line in lines:
   if ':' in line:
    k,v=line.split(':',1)
    k=k.strip(); v=v.strip()
    if k and v and len(k)<36:
     pairs.append((k,v)); continue
   other.append(line)
  tk.Label(bubble,text=title,bg=bubble['bg'],fg=self.app.C['green'],
           font=(self.app.font,max(8,self.app.fs-1),'bold')).pack(anchor='w',padx=14,pady=(11,7))
  grid=tk.Frame(bubble,bg=bubble['bg']); grid.pack(fill='x',padx=14,pady=(0,7))
  for k,v in pairs[:20]:
   r=tk.Frame(grid,bg=bubble['bg']); r.pack(fill='x',pady=2)
   tk.Label(r,text=k,bg=bubble['bg'],fg=self.app.C['muted'],anchor='w',
            font=(self.app.font,max(8,self.app.fs-1))).pack(side='left')
   tk.Label(r,text=v,bg=bubble['bg'],fg=self.app.C['text'],anchor='e',
            font=(self.app.font,max(8,self.app.fs-1),'bold')).pack(side='right')
  if other:
   tk.Label(bubble,text='\n'.join(other),bg=bubble['bg'],fg=self.app.C['text'],
            justify='left',anchor='w',wraplength=760,font=(self.app.font,self.app.fs)).pack(anchor='w',padx=14,pady=(0,11))
  elif not pairs:
   tk.Label(bubble,text=self._clean_markdown(text),bg=bubble['bg'],fg=self.app.C['text'],
            justify='left',anchor='w',wraplength=760,font=(self.app.font,self.app.fs)).pack(anchor='w',padx=14,pady=(0,11))
 def _action_card(self,bubble,text):
  desc=text.replace('CONFIRM:','',1).replace('approve / cancel','').strip()
  tk.Label(bubble,text='ACTION CONFIRMATION',bg=bubble['bg'],fg=self.app.C['yellow'],
           font=(self.app.font,max(8,self.app.fs-1),'bold')).pack(anchor='w',padx=14,pady=(11,4))
  tk.Label(bubble,text=desc,bg=bubble['bg'],fg=self.app.C['text'],justify='left',
           wraplength=700,font=(self.app.font,self.app.fs)).pack(anchor='w',padx=14,pady=(0,10))
  buttons=tk.Frame(bubble,bg=bubble['bg']); buttons.pack(anchor='w',padx=14,pady=(0,12))
  tk.Button(buttons,text='Approve',command=lambda:self.app.submit_text('approve'),
            bg=self.app.C['accent'],fg='white',bd=0,padx=14,pady=7).pack(side='left',padx=(0,7))
  tk.Button(buttons,text='Cancel',command=lambda:self.app.submit_text('cancel'),
            bg=self.app.C['surface2'],fg=self.app.C['text'],bd=0,padx=14,pady=7).pack(side='left')
 def _clean_markdown(self,text):
  text=re.sub(r'(?m)^\s*#{1,6}\s*','',text or '')
  text=text.replace('**','').replace('__','').replace('`','')
  text=re.sub(r'(?m)^---+$','',text)
  text=re.sub(r'\n{3,}','\n\n',text).strip()
  return text

class App:
 def __init__(self,root):
  self.root=root; self.settings=load_settings(); self.events=queue.Queue(); self.responses=queue.Queue(); self.session=engine.Session(); self.busy=False; self.closing=False; self.metrics_inflight=False; self.current='Chat'; self.nav={}; self.metric_refs={}; self.last_gpu=None; self.last_gpu_t=0
  self.chat_history=[]; self.queued_voice_text=None; self.voice_button=None; self.voice=VoiceController(self); self.apply_palette(); self.root.title('MyLocalAI v12.2 — Adaptive Self-Evolution'); self.root.geometry('1280x820'); self.root.minsize(980,650); self.build(); self.show('Chat'); self.root.after(75,self.poll); self.root.after(500,self.refresh_metrics); self.root.protocol('WM_DELETE_WINDOW',self.close)
 def apply_palette(self):
  s=self.settings; self.C={'bg':s['background'],'surface':s['surface'],'surface2':'#18233b','sidebar':'#0f172a','border':'#26334d','text':s['text'],'muted':s['muted'],'accent':s['accent'],'teal':'#38d6c8','green':'#4ade80','yellow':'#fbbf24','red':'#fb7185'}; self.font=s['font']; self.fs=int(s['font_size'])
 def build(self):
  C=self.C; self.root.configure(bg=C['bg']); header=tk.Frame(self.root,bg=C['sidebar'],height=62); header.pack(fill='x'); header.pack_propagate(False)
  tk.Label(header,text='◉',fg=C['teal'],bg=C['sidebar'],font=(self.font,20,'bold')).pack(side='left',padx=(20,7)); tk.Label(header,text='MyLocalAI',fg=C['text'],bg=C['sidebar'],font=(self.font,18,'bold')).pack(side='left'); tk.Label(header,text='V12.2 • SELF-EVOLUTION',fg=C['muted'],bg=C['sidebar'],font=(self.font,8,'bold')).pack(side='left',padx=12)
  self.status=tk.Label(header,text='● LOCAL',fg=C['green'],bg=C['sidebar'],font=(self.font,9,'bold')); self.status.pack(side='right',padx=20); self.model=tk.Label(header,text='qwen3:8b',fg=C['muted'],bg=C['sidebar']); self.model.pack(side='right',padx=14)
  shell=tk.Frame(self.root,bg=C['bg']); shell.pack(fill='both',expand=True); side=tk.Frame(shell,bg=C['sidebar'],width=220); side.pack(side='left',fill='y'); side.pack_propagate(False)
  tk.Label(side,text='WORKSPACE',fg=C['muted'],bg=C['sidebar'],font=(self.font,8,'bold')).pack(anchor='w',padx=20,pady=(24,8))
  for n,i in [('Chat','💬'),('PC Overview','🖥'),('Performance','📊'),('Tasks','✓'),('Memory','🧠'),('Learning','↗'),('Research','🌐'),('Evolution','⚙'),('Voice','🎤')]: self.nav_button(side,n,i)
  tk.Frame(side,bg=C['border'],height=1).pack(fill='x',padx=16,pady=14); self.nav_button(side,'Settings','⚙')
  self.content=tk.Frame(shell,bg=C['bg']); self.content.pack(side='left',fill='both',expand=True)
  foot=tk.Frame(self.root,bg=C['sidebar'],height=30); foot.pack(fill='x'); foot.pack_propagate(False); self.voice_status=tk.Label(foot,text='🎤 Voice Ready',fg=C['muted'],bg=C['sidebar'],font=(self.font,8)); self.voice_status.pack(side='left',padx=14); tk.Label(foot,text='LOCAL • PRIVATE • RTX READY',fg=C['muted'],bg=C['sidebar'],font=(self.font,8)).pack(side='right',padx=14)
 def nav_button(self,parent,name,icon):
  C=self.C; b=tk.Button(parent,text=f' {icon}   {name}',anchor='w',bd=0,relief='flat',bg=C['sidebar'],fg=C['muted'],activebackground=C['surface'],activeforeground=C['text'],font=(self.font,self.fs,'bold'),command=lambda:self.show(name),padx=18,pady=10); b.pack(fill='x',padx=8,pady=2); self.nav[name]=b
 def clear(self):
  for w in self.content.winfo_children(): w.destroy()
 def title(self,title,sub):
  C=self.C; tk.Label(self.content,text=title,bg=C['bg'],fg=C['text'],font=(self.font,22,'bold')).pack(anchor='w',padx=28,pady=(24,2)); tk.Label(self.content,text=sub,bg=C['bg'],fg=C['muted'],font=(self.font,self.fs)).pack(anchor='w',padx=29,pady=(0,16))
 def show(self,name):
  self.current=name; self.clear(); C=self.C
  for n,b in self.nav.items(): b.configure(fg=C['text'] if n==name else C['muted'],bg=C['surface'] if n==name else C['sidebar'])
  {'Chat':self.page_chat,'PC Overview':self.page_overview,'Performance':self.page_performance,'Tasks':lambda:self.page_commands('Tasks','Manage your local tasks.', ['tasks','complete task 1']),'Memory':lambda:self.page_commands('Memory','Persistent local memory.', ['what do you remember','notes','projects']),'Learning':self.page_learning,'Research':self.page_research,'Evolution':self.page_evolution,'Voice':self.page_voice,'Settings':self.page_settings}[name]()
 def page_chat(self):
  C=self.C; self.title('Chat','')
  self.chat_view=ChatView(self.content,self)
  self.chat_view.pack(fill='both',expand=True,padx=(18,12),pady=(0,8))
  if not self.chat_history:
   self.chat_history.append(('MYLOCALAI','Ready. How can I help?','ai'))
  for who,text,kind in self.chat_history:
   self.chat_view.add(who,text,kind)
  bar=tk.Frame(self.content,bg=C['bg']); bar.pack(fill='x',padx=28,pady=(0,22))
  self.entry=tk.Entry(bar,bg=C['surface'],fg=C['text'],insertbackground=C['text'],
                      relief='flat',highlightbackground=C['border'],highlightthickness=1,
                      font=(self.font,self.fs+1))
  self.entry.pack(side='left',fill='x',expand=True,ipady=12)
  self.entry.bind('<Return>',lambda e:self.send())
  self.voice_button=tk.Button(bar,text='🎤',command=self.toggle_chat_voice,bg=C['surface2'],fg=C['text'],
            activebackground=C['border'],activeforeground=C['text'],bd=0,font=(self.font,14),padx=12)
  self.voice_button.pack(side='left',padx=7)
  self.send_btn=tk.Button(bar,text='Send ➜',command=self.send,bg=C['accent'],fg='white',
                          bd=0,font=(self.font,self.fs,'bold'),padx=18,pady=10)
  self.send_btn.pack(side='left')
  self.entry.focus_set()
 def append(self,who,text,kind='ai',feedback=False,feedback_id=None):
  text=(text or '').strip()
  if not text: return
  self.chat_history.append((who,text,kind))
  if hasattr(self,'chat_view') and self.chat_view.winfo_exists():
   self.chat_view.add(who,text,kind,feedback=feedback,feedback_id=feedback_id)
 def toggle_chat_voice(self):
  """Toggle local voice capture from the main Chat page."""
  try:
   if self.voice.listening:
    self.voice.stop_listening()
    return
   device=None
   if hasattr(self,'voice_mic_var'):
    try: device=self.selected_microphone_index()
    except Exception: device=None
   if device is None:
    device=self.voice.selected_device(self.settings.get('voice_mic_choice','Default microphone'))
   self.voice.listen(device)
   if hasattr(self,'voice_button') and self.voice_button and self.voice_button.winfo_exists():
    self.voice_button.configure(text='■',state='normal')
  except Exception as e:
   logger.exception('Main chat voice start failed')
   self.events.put(('voice_error',f'Could not start voice input: {type(e).__name__}: {e}'))

 def submit_text(self,text):
  text=(text or '').strip()
  if not text: return
  if self.busy:
   self.queued_voice_text=text
   if self.current=='Chat': self.append('SYSTEM', 'Voice command queued until the current response finishes.', 'system')
   return
  if self.current!='Chat': self.show('Chat')
  if hasattr(self,'entry') and self.entry.winfo_exists():
   self.entry.delete(0,'end'); self.entry.insert(0,text); self.send()
 def send(self):
  if self.busy:return
  if not hasattr(self,'entry') or not self.entry.winfo_exists(): return
  t=self.entry.get().strip()
  if not t:return
  self.entry.delete(0,'end'); self.append('YOU',t,'you'); self.busy=True; self.status.configure(text='● THINKING',fg=self.C['yellow']); self.send_btn.configure(state='disabled')
  def work():
   try: self.responses.put(engine.handle_message(t,self.session))
   except Exception as e:
    logger.exception('AI request failed')
    self.responses.put(('error',f'{type(e).__name__}: {e}'))
  threading.Thread(target=work,daemon=True,name='AIWorker').start()
 def rate_response(self,interaction_id,score):
  try:
   if interaction_id is not None:
    self.session.last_interaction_id=int(interaction_id)
   engine.record_feedback(self.session,score)
   self.voice_status.configure(text='✓ Feedback saved locally')
  except Exception as e:
   logger.exception('Feedback failed')
   self.voice_status.configure(text='Feedback error')

 def page_overview(self):
  self.title('PC Overview','Live snapshot from your local machine.')
  row=tk.Frame(self.content,bg=self.C['bg']); row.pack(fill='x',padx=22)
  for key,title,sub,col in [('cpu','CPU','Live usage',self.C['accent']),('gpu','GPU','RTX metrics',self.C['teal']),('ram','RAM','Memory',self.C['yellow']),('disk','Storage','Disk',self.C['green'])]: self.metric_card(row,key,title,sub,col)
  c=Card(self.content,self); c.pack(fill='both',expand=True,padx=28,pady=18); tk.Label(c,text='Hardware report',bg=self.C['surface'],fg=self.C['text'],font=(self.font,13,'bold')).pack(anchor='w',padx=18,pady=(16,8)); tk.Button(c,text='Refresh full specs',command=lambda:self.run_command('specs',self.output),bg=self.C['accent'],fg='white',bd=0).pack(anchor='w',padx=18,pady=(0,8)); self.output=self.output_box(c); self.run_command('specs',self.output)
 def metric_card(self,parent,key,title,sub,col):
  c=Card(parent,self,width=210,height=118); c.pack(side='left',fill='both',expand=True,padx=6); c.pack_propagate(False); tk.Label(c,text=title.upper(),bg=self.C['surface'],fg=self.C['muted'],font=(self.font,8,'bold')).pack(anchor='w',padx=16,pady=(15,2)); v=tk.Label(c,text='Loading…',bg=self.C['surface'],fg=self.C['text'],font=(self.font,20,'bold')); v.pack(anchor='w',padx=16); s=tk.Label(c,text=sub,bg=self.C['surface'],fg=col,font=(self.font,9)); s.pack(anchor='w',padx=16); self.metric_refs[key]=(v,s)
 def page_performance(self):
  self.title('Performance','Live utilization and diagnostics.')
  row=tk.Frame(self.content,bg=self.C['bg']); row.pack(fill='x',padx=22)
  for key,title,sub,col in [('cpu','CPU','Usage',self.C['accent']),('gpu','GPU','Usage / temp',self.C['teal']),('ram','RAM','Used',self.C['yellow']),('disk','Storage','Disk',self.C['green'])]: self.metric_card(row,key,title,sub,col)
  c=Card(self.content,self); c.pack(fill='both',expand=True,padx=28,pady=18); tk.Button(c,text='Run diagnostics',command=lambda:self.run_command('diagnostics',self.output),bg=self.C['accent'],fg='white',bd=0).pack(anchor='w',padx=18,pady=16); self.output=self.output_box(c)
 def output_box(self,parent):
  box=tk.Frame(parent,bg=self.C['surface'])
  box.pack(fill='both',expand=True,padx=12,pady=10)
  sb=tk.Scrollbar(box,orient='vertical',bg=self.C['surface2'],activebackground=self.C['border'],troughcolor=self.C['surface'],highlightthickness=0,bd=0)
  sb.pack(side='right',fill='y')
  o=tk.Text(box,bg=self.C['surface'],fg='#cbd5e1',relief='flat',wrap='word',font=('Consolas',9),state='disabled',yscrollcommand=sb.set)
  o.pack(side='left',fill='both',expand=True); sb.config(command=o.yview); return o
 def page_commands(self,title,sub,cmds):
  self.title(title,sub); c=Card(self.content,self); c.pack(fill='both',expand=True,padx=28,pady=18); row=tk.Frame(c,bg=self.C['surface']); row.pack(fill='x',padx=18,pady=18); out=self.output_box(c)
  for cmd in cmds: tk.Button(row,text=cmd,command=lambda x=cmd:self.run_command(x,out),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=12,pady=8).pack(side='left',padx=4)
 def run_command(self,cmd,out):
  def work():
   try: _,text=engine.handle_message(cmd,self.session)
   except Exception as e:
    logger.exception('PC command failed: %s',cmd)
    text=f'{type(e).__name__}: {e}'
   self.events.put(('output',(out,text)))
  threading.Thread(target=work,daemon=True).start()
 def page_learning(self):
  self.title('Learning','Local memory, feedback, and learned command habits.')
  top=Card(self.content,self); top.pack(fill='x',padx=28,pady=(0,12))
  stats=engine.get_learning_stats()
  tk.Label(top,text='LOCAL LEARNING',bg=self.C['surface'],fg=self.C['teal'],font=(self.font,9,'bold')).pack(anchor='w',padx=18,pady=(16,5))
  tk.Label(top,text=stats,bg=self.C['surface'],fg=self.C['text'],justify='left',font=(self.font,self.fs)).pack(anchor='w',padx=18,pady=(0,14))
  row=tk.Frame(top,bg=self.C['surface']); row.pack(anchor='w',padx=18,pady=(0,15))
  for label,cmd in [('Learning stats','learning stats'),('View learned rules','learned rules'),('Evolution suggestions','suggestions')]:
   tk.Button(row,text=label,command=lambda c=cmd:self.run_command(c,self.learning_output),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=12,pady=8).pack(side='left',padx=(0,8))
  teach=Card(self.content,self); teach.pack(fill='x',padx=28,pady=12)
  tk.Label(teach,text='Teach a safe command habit',bg=self.C['surface'],fg=self.C['text'],font=(self.font,13,'bold')).pack(anchor='w',padx=18,pady=(16,6))
  tk.Label(teach,text="Example: teach when I say 'gaming time' do 'open steam'",bg=self.C['surface'],fg=self.C['muted']).pack(anchor='w',padx=18,pady=(0,8))
  entry=tk.Entry(teach,bg=self.C['surface2'],fg=self.C['text'],insertbackground=self.C['text'],relief='flat',font=(self.font,self.fs))
  entry.pack(fill='x',padx=18,pady=(0,10),ipady=8)
  tk.Button(teach,text='Teach',command=lambda:self.submit_text(entry.get()),bg=self.C['accent'],fg='white',bd=0,padx=14,pady=8).pack(anchor='w',padx=18,pady=(0,16))
  box=Card(self.content,self); box.pack(fill='both',expand=True,padx=28,pady=(0,18))
  self.learning_output=self.output_box(box)
  self.learning_output.configure(state='normal'); self.learning_output.insert('end',stats); self.learning_output.configure(state='disabled')

 def page_evolution(self):
  self.title('Self-Evolution','Let MyLocalAI diagnose, test, and improve its own implementation.')
  box=Card(self.content,self); box.pack(fill='x',padx=28,pady=(0,12))
  tk.Label(box,text='CONTROLLED AUTONOMY',bg=self.C['surface'],fg=self.C['teal'],font=(self.font,9,'bold')).pack(anchor='w',padx=18,pady=(16,5))
  tk.Label(box,text='Guided mode proposes validated source changes. Autonomous mode may auto-apply only low-risk allow-listed files after backup + syntax + smoke validation.',wraplength=900,justify='left',bg=self.C['surface'],fg=self.C['muted'],font=(self.font,self.fs)).pack(anchor='w',padx=18,pady=(0,12))
  row=tk.Frame(box,bg=self.C['surface']); row.pack(anchor='w',padx=18,pady=(0,16))
  for label,cmd in [('Run evolution','self evolve'),('Autonomous ON','self evolution on'),('Guided mode','self evolution off'),('Status','self status'),('Rollback latest','self rollback')]:
   tk.Button(row,text=label,command=lambda c=cmd:self.submit_text(c),bg=self.C['accent'] if 'ON' in label else self.C['surface2'],fg='white' if 'ON' in label else self.C['text'],bd=0,padx=12,pady=8).pack(side='left',padx=(0,8))
  info=Card(self.content,self); info.pack(fill='both',expand=True,padx=28,pady=(0,18))
  self.evolution_output=self.output_box(info)
  try:
   _,text=engine.handle_message('self status',self.session)
   self.evolution_output.configure(state='normal'); self.evolution_output.insert('end',text); self.evolution_output.configure(state='disabled')
  except Exception as e:
   self.evolution_output.configure(state='normal'); self.evolution_output.insert('end',f'{type(e).__name__}: {e}'); self.evolution_output.configure(state='disabled')
 def page_research(self):
  self.clear(); self.title('Research & Observation','Controlled internet access, coding knowledge, and opt-in screen habit learning.')
  box=tk.Frame(self.content,bg=self.C['surface']); box.pack(fill='x',padx=18,pady=12)
  tk.Label(box,text='Internet access',bg=self.C['surface'],fg=self.C['text'],font=(self.font,14,'bold')).pack(anchor='w',padx=20,pady=(16,4))
  tk.Label(box,text='OFF by default. Public HTTP/HTTPS only. Local/private network addresses are blocked.',bg=self.C['surface'],fg=self.C['muted'],font=(self.font,self.fs)).pack(anchor='w',padx=20,pady=(0,10))
  row=tk.Frame(box,bg=self.C['surface']); row.pack(fill='x',padx=20,pady=5)
  for label,cmd in [('Turn Internet ON','internet on'),('Turn OFF','internet off'),('Search Web…',None),('Study URL…',None),('Study GitHub…',None)]:
   if cmd: tk.Button(row,text=label,command=lambda c=cmd:self.submit_text(c),bg=self.C['accent'] if 'ON' in label else self.C['surface2'],fg='white' if 'ON' in label else self.C['text'],bd=0,padx=14,pady=8).pack(side='left',padx=(0,8))
   elif 'Search' in label: tk.Button(row,text=label,command=self.research_search,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=8).pack(side='left',padx=(0,8))
   elif 'URL' in label: tk.Button(row,text=label,command=self.research_url,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=8).pack(side='left',padx=(0,8))
   else: tk.Button(row,text=label,command=self.research_github,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=8).pack(side='left')
  kb=tk.Frame(self.content,bg=self.C['surface']); kb.pack(fill='x',padx=18,pady=8)
  tk.Label(kb,text='Local coding / AI knowledge',bg=self.C['surface'],fg=self.C['text'],font=(self.font,14,'bold')).pack(anchor='w',padx=20,pady=(16,4))
  tk.Label(kb,text='Study public docs, websites, and GitHub source. Relevant chunks are retrieved locally and added as context to matching chats.',wraplength=900,justify='left',bg=self.C['surface'],fg=self.C['muted'],font=(self.font,self.fs)).pack(anchor='w',padx=20,pady=(0,8))
  self.knowledge_out=self.output_box(kb)
  try:
   self.knowledge_out.configure(state='normal'); self.knowledge_out.insert('end',json.dumps(engine._KNOWLEDGE_STORE.stats(),indent=2)); self.knowledge_out.configure(state='disabled')
  except Exception: pass
  ob=tk.Frame(self.content,bg=self.C['surface']); ob.pack(fill='x',padx=18,pady=8)
  tk.Label(ob,text='Screen habit learning',bg=self.C['surface'],fg=self.C['text'],font=(self.font,14,'bold')).pack(anchor='w',padx=20,pady=(16,4))
  tk.Label(ob,text='OFF by default. Background mode stores foreground app/title timing only. Sensitive-looking windows are excluded and screenshots are not retained by the background observer.',wraplength=900,justify='left',bg=self.C['surface'],fg=self.C['muted'],font=(self.font,self.fs)).pack(anchor='w',padx=20,pady=(0,8))
  rr=tk.Frame(ob,bg=self.C['surface']); rr.pack(fill='x',padx=20,pady=6)
  tk.Button(rr,text='Start Screen Learning',command=lambda:self.submit_text('screen on'),bg=self.C['accent'],fg='white',bd=0,padx=14,pady=8).pack(side='left')
  tk.Button(rr,text='Stop',command=lambda:self.submit_text('screen off'),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=8).pack(side='left',padx=8)
  tk.Button(rr,text='Status',command=lambda:self.submit_text('screen status'),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=8).pack(side='left')
  tk.Button(rr,text='Repeated Transitions',command=lambda:self.submit_text('screen transitions'),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=8).pack(side='left',padx=8)
  tk.Button(rr,text='One-time Screenshot',command=lambda:self.submit_text('screen snapshot'),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=8).pack(side='left')
 def research_search(self):
  from tkinter import simpledialog
  q=simpledialog.askstring('Web Search','What should MyLocalAI search for?',parent=self.root)
  if q: self.submit_text('search web '+q)
 def research_url(self):
  from tkinter import simpledialog
  u=simpledialog.askstring('Study URL','Public URL to study:',parent=self.root)
  if u: self.submit_text('study '+u)
 def research_github(self):
  from tkinter import simpledialog
  r=simpledialog.askstring('Study GitHub','Repository owner/name:',parent=self.root)
  if r: self.submit_text('study github '+r)
 def page_voice(self):
  self.title('Voice','Talk to MyLocalAI with your microphone and hear responses.')
  top=Card(self.content,self); top.pack(fill='x',padx=28,pady=(6,8))
  missing=self.voice.check()
  mics=self.voice.microphones()
  ok=not missing and bool(mics)
  problems=list(missing)
  if not mics: problems.append('No input microphone detected')
  status_text=('Voice system ready.' if ok else 'Voice setup incomplete: '+', '.join(problems))
  self.voice_info=tk.Label(top,text=status_text,bg=self.C['surface'],fg=(self.C['green'] if ok else self.C['yellow']),font=(self.font,self.fs,'bold'))
  self.voice_info.pack(anchor='w',padx=20,pady=(16,4))
  self.voice_detail=tk.Label(top,text='Input: Local faster-whisper • Output: Windows pyttsx3',bg=self.C['surface'],fg=self.C['muted'],font=(self.font,max(8,self.fs-1)))
  self.voice_detail.pack(anchor='w',padx=20,pady=(0,12))

  controls=tk.Frame(top,bg=self.C['surface']); controls.pack(fill='x',padx=20,pady=(0,8))
  tk.Label(controls,text='Microphone',bg=self.C['surface'],fg=self.C['muted']).pack(side='left')
  self.voice_mics=mics
  choices=['Default microphone']+[f'{i}: {name}' for i,name in mics]
  saved=str(self.settings.get('voice_mic_choice','Default microphone'))
  self.voice_mic_var=tk.StringVar(value=(saved if saved in choices else 'Default microphone'))
  self.voice_mic_combo=ttk.Combobox(controls,textvariable=self.voice_mic_var,values=choices,state='readonly',width=48)
  self.voice_mic_combo.bind('<<ComboboxSelected>>',lambda e:self.update_voice_setting('voice_mic_choice',self.voice_mic_var.get()))
  self.voice_mic_combo.pack(side='left',padx=10)

  row=tk.Frame(top,bg=self.C['surface']); row.pack(anchor='w',padx=20,pady=(4,18))
  self.voice_listen_btn=tk.Button(row,text='🎤 Start Listening',command=self.start_voice_listening,bg=self.C['accent'],fg='white',bd=0,padx=18,pady=10)
  self.voice_listen_btn.pack(side='left',padx=(0,8))
  tk.Button(row,text='Run Voice Diagnostics',command=self.run_voice_diagnostics,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=10).pack(side='left',padx=4)
  tk.Button(row,text='Load Whisper',command=self.voice.warmup,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=10).pack(side='left',padx=4)
  tk.Button(row,text='Test Microphone',command=self.test_voice_microphone,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=10).pack(side='left',padx=4)
  tk.Button(row,text='Test Recognition',command=self.test_voice_recognition,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=10).pack(side='left',padx=4)
  tk.Button(row,text='Test Voice',command=lambda:self.voice.speak('Voice output is working. MyLocalAI is ready.',force=True),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=10).pack(side='left',padx=4)
  tk.Button(row,text='Stop Listening',command=self.voice.stop_listening,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=10).pack(side='left',padx=4)
  tk.Button(row,text='🔇 Stop Speaking',command=self.voice.stop_speaking,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=14,pady=10).pack(side='left',padx=4)

  mid=Card(self.content,self); mid.pack(fill='both',expand=True,padx=28,pady=10)
  tk.Label(mid,text='Live voice activity',bg=self.C['surface'],fg=self.C['text'],font=(self.font,13,'bold')).pack(anchor='w',padx=20,pady=(16,8))
  self.voice_transcript=tk.Text(mid,height=7,bg=self.C['surface2'],fg=self.C['text'],insertbackground=self.C['text'],relief='flat',wrap='word',font=(self.font,self.fs))
  self.voice_transcript.pack(fill='x',padx=20,pady=(0,12))
  self.voice_transcript.insert('end','Press Start Listening, then speak. Recognized text will appear here and be sent to Chat automatically.')
  self.voice_transcript.configure(state='disabled')

  self.voice_error_label=tk.Label(mid,text='',justify='left',wraplength=900,bg=self.C['surface'],fg=self.C['yellow'],font=(self.font,self.fs))
  self.voice_error_label.pack(anchor='w',padx=20,pady=(0,12))

  opts=tk.Frame(mid,bg=self.C['surface']); opts.pack(fill='x',padx=20,pady=(4,18))
  self.voice_input_var=tk.BooleanVar(value=bool(self.settings.get('voice_input',True)))
  self.voice_speak_var=tk.BooleanVar(value=bool(self.settings.get('speak_responses',True)))
  tk.Checkbutton(opts,text='Enable voice input',variable=self.voice_input_var,bg=self.C['surface'],fg=self.C['text'],selectcolor=self.C['surface2'],activebackground=self.C['surface'],command=lambda:self.update_voice_setting('voice_input',self.voice_input_var.get())).pack(side='left',padx=(0,18))
  tk.Checkbutton(opts,text='Speak AI responses automatically',variable=self.voice_speak_var,bg=self.C['surface'],fg=self.C['text'],selectcolor=self.C['surface2'],activebackground=self.C['surface'],command=lambda:self.update_voice_setting('speak_responses',self.voice_speak_var.get())).pack(side='left')
  note='Local voice pipeline: microphone → signal analysis → mono → 16 kHz → float32 normalization → faster-whisper. Tests save the latest capture to data/voice_debug/last_capture.wav for troubleshooting. No Google speech service or PyAudio is used. Whisper may need a one-time model download before recognition can run offline.'
  tk.Label(mid,text=note,justify='left',wraplength=950,bg=self.C['surface'],fg=self.C['muted'],font=(self.font,max(8,self.fs-1))).pack(anchor='w',padx=20,pady=(0,18))

 def update_voice_setting(self,key,value):
  self.settings[key]=value
  save_settings(self.settings)

 def selected_microphone_index(self):
  try:
   choice = self.voice_mic_var.get() if hasattr(self,'voice_mic_var') else self.settings.get('voice_mic_choice','Default microphone')
   return self.voice.selected_device(choice)
  except Exception:
   return None

 def start_voice_listening(self):
  if not self.settings.get('voice_input',True):
   self.settings['voice_input']=True
   save_settings(self.settings)
   if hasattr(self,'voice_input_var'): self.voice_input_var.set(True)
  if hasattr(self,'voice_error_label') and self.voice_error_label.winfo_exists():
   self.voice_error_label.configure(text='')
  self.voice.listen(self.selected_microphone_index())

 def test_voice_microphone(self):
  if hasattr(self,'voice_error_label') and self.voice_error_label.winfo_exists(): self.voice_error_label.configure(text='')
  self.voice.test_microphone(self.selected_microphone_index())

 def test_voice_recognition(self):
  if hasattr(self,'voice_error_label') and self.voice_error_label.winfo_exists(): self.voice_error_label.configure(text='')
  self.voice.test_recognition(self.selected_microphone_index())

 def run_voice_diagnostics(self):
  def work():
   lines=['VOICE DIAGNOSTICS','='*52]
   missing=self.voice.check()
   lines.append('Python packages: '+('PASS' if not missing else 'FAIL — '+', '.join(missing)))
   try:
    import sounddevice as sd
    m=[(i,d['name']) for i,d in enumerate(sd.query_devices()) if d.get('max_input_channels',0)>0]
    lines.append(f'Audio backend: PASS • {len(m)} input device(s)')
    for i,n in m[:8]: lines.append(f'  [{i}] {n}')
   except Exception as e: lines.append('Audio backend: FAIL — '+str(e))
   try:
    with com_thread_context():
     import pyttsx3; t=pyttsx3.init(); names=t.getProperty('voices') or []; lines.append(f'TTS: PASS • {len(names)} voice(s)'); t.stop()
   except Exception as e: lines.append('TTS: FAIL — '+str(e))
   try:
    import faster_whisper; lines.append('faster-whisper import: PASS'); lines.append('Whisper model: '+self.voice._model_name()+' (click Load / Test Whisper to validate)')
   except Exception as e: lines.append('faster-whisper import: FAIL — '+str(e))
   try:
    import ctranslate2; c=getattr(ctranslate2,'get_cuda_device_count',lambda:0)(); lines.append(f'CUDA backend: {c} device(s) detected')
   except Exception as e: lines.append('CUDA backend: unavailable — '+str(e))
   self.events.put(('voice_diagnostics','\n'.join(lines)))
  threading.Thread(target=work,daemon=True,name='VoiceDiagnostics').start()

 def page_settings(self):
  self.title('Settings','Appearance, accessibility, voice, and AI performance.')
  outer=tk.Frame(self.content,bg=self.C['bg']); outer.pack(fill='both',expand=True,padx=28,pady=4)
  left=Card(outer,self); left.pack(side='left',fill='both',expand=True,padx=(0,8)); right=Card(outer,self); right.pack(side='left',fill='both',expand=True,padx=(8,0))
  tk.Label(left,text='Appearance',bg=self.C['surface'],fg=self.C['text'],font=(self.font,14,'bold')).pack(anchor='w',padx=20,pady=(18,10))
  self.setting_combo(left,'Theme','theme',list(THEMES)); self.color_row(left,'Accent color','accent'); self.color_row(left,'Background','background'); self.color_row(left,'Text color','text'); self.setting_combo(left,'Font','font',['Segoe UI','Arial','Calibri','Consolas'])
  self.slider(left,'Text size','font_size',8,18); self.slider(left,'UI scale','ui_scale',80,140); tk.Button(left,text='Apply appearance',command=self.apply_settings,bg=self.C['accent'],fg='white',bd=0,padx=16,pady=9).pack(anchor='w',padx=20,pady=14)
  tk.Label(right,text='AI & Voice',bg=self.C['surface'],fg=self.C['text'],font=(self.font,14,'bold')).pack(anchor='w',padx=20,pady=(18,10)); self.setting_combo(right,'Performance profile','performance',['Fast','Balanced','Deep'])
  self.bool_row(right,'Enable voice input','voice_input'); self.bool_row(right,'Speak AI responses','speak_responses'); self.slider(right,'Speech rate','speech_rate',100,250); self.slider(right,'Speech volume','speech_volume',0,1)
  tk.Button(right,text='Save AI & Voice settings',command=self.save_runtime_settings,bg=self.C['accent'],fg='white',bd=0,padx=16,pady=9).pack(anchor='w',padx=20,pady=(4,8))
  tk.Label(right,text='Performance profile is now descriptive only until provider-level generation controls are explicitly wired. It will not silently claim to speed up or deepen the model.',wraplength=420,justify='left',bg=self.C['surface'],fg=self.C['muted'],font=(self.font,self.fs)).pack(anchor='w',padx=20,pady=14)
  tk.Button(right,text='Open Logs Folder',command=self.open_logs,bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=16,pady=9).pack(anchor='w',padx=20,pady=(0,8))
  tk.Button(right,text='Check local model',command=lambda:self.run_command('model',self.settings_out),bg=self.C['surface2'],fg=self.C['text'],bd=0,padx=16,pady=9).pack(anchor='w',padx=20,pady=(0,8)); self.settings_out=self.output_box(right)
 def setting_combo(self,parent,label,key,vals):
  row=tk.Frame(parent,bg=self.C['surface']); row.pack(fill='x',padx=20,pady=6); tk.Label(row,text=label,bg=self.C['surface'],fg=self.C['muted']).pack(side='left'); v=tk.StringVar(value=str(self.settings[key])); cb=ttk.Combobox(row,textvariable=v,values=vals,state='readonly'); cb.pack(side='right'); cb.bind('<<ComboboxSelected>>',lambda e,k=key,x=v:self.settings.__setitem__(k,x.get()))
 def color_row(self,parent,label,key):
  row=tk.Frame(parent,bg=self.C['surface']); row.pack(fill='x',padx=20,pady=6); tk.Label(row,text=label,bg=self.C['surface'],fg=self.C['muted']).pack(side='left'); b=tk.Button(row,text=self.settings[key],command=lambda k=key:self.pick_color(k),bg=self.settings[key],fg='white',bd=0,padx=8); b.pack(side='right')
 def pick_color(self,key):
  x=colorchooser.askcolor(color=self.settings[key],parent=self.root)[1]
  if x:
   self.settings[key]=x
   save_settings(self.settings)
 def slider(self,parent,label,key,lo,hi):
  row=tk.Frame(parent,bg=self.C['surface']); row.pack(fill='x',padx=20,pady=6); tk.Label(row,text=label,bg=self.C['surface'],fg=self.C['muted']).pack(anchor='w'); v=tk.DoubleVar(value=float(self.settings[key])) if (hi<=1 or isinstance(self.settings.get(key),float)) else tk.IntVar(value=int(self.settings[key])); tk.Scale(row,from_=lo,to=hi,resolution=(0.05 if hi<=1 else 1),orient='horizontal',variable=v,bg=self.C['surface'],fg=self.C['text'],highlightthickness=0,command=lambda x,k=key,var=v:self.settings.__setitem__(k,var.get())).pack(fill='x')
 def bool_row(self,parent,label,key):
  v=tk.BooleanVar(value=bool(self.settings[key])); tk.Checkbutton(parent,text=label,variable=v,bg=self.C['surface'],fg=self.C['text'],selectcolor=self.C['surface2'],activebackground=self.C['surface'],command=lambda:self.settings.__setitem__(key,v.get())).pack(anchor='w',padx=20,pady=7)
 def open_logs(self):
  try:
   import subprocess
   subprocess.Popen(['explorer',LOG_DIR],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  except Exception as e: messagebox.showerror('MyLocalAI','Could not open logs folder: '+str(e))

 def save_runtime_settings(self):
  save_settings(self.settings)
  missing=self.voice.check()
  msg='AI and voice settings saved.'
  if missing: msg+='\\n\\nMissing voice components: '+', '.join(missing)+'\\nRun: Install Voice Dependencies.bat'
  else: msg+='\\n\\nVoice components detected and ready.'
  messagebox.showinfo('MyLocalAI',msg)
 def apply_settings(self):
  theme=self.settings.get('theme');
  if theme in THEMES:
   for k,v in THEMES[theme].items(): self.settings[k]=v
  save_settings(self.settings); messagebox.showinfo('MyLocalAI','Appearance saved. Restart is recommended so every widget is rebuilt consistently.')
 def refresh_metrics(self):
  if self.closing:return
  if not self.metrics_inflight:
   self.metrics_inflight=True
   def work():
    try:
     cpu=psutil.cpu_percent(interval=.4); vm=psutil.virtual_memory(); du=psutil.disk_usage(os.environ.get('SystemDrive','C:')+'\\'); g=None
     if time.time()-self.last_gpu_t>4 or self.last_gpu is None:self.last_gpu=gpu_metrics(); self.last_gpu_t=time.time()
     g=self.last_gpu; self.events.put(('metrics',{'cpu':cpu,'ram':vm,'disk':du,'gpu':g}))
    except Exception as e:
     logger.exception('Metrics refresh failed')
     self.events.put(('metrics_error',str(e)))
    finally:
     self.metrics_inflight=False
   threading.Thread(target=work,daemon=True,name='Metrics').start()
  self.root.after(2000,self.refresh_metrics)
 def set_metric(self,key,value,sub):
  if key in self.metric_refs:
   a,b=self.metric_refs[key]
   if a.winfo_exists(): a.configure(text=value); b.configure(text=sub)
 def poll(self):
  try:
   while True:
    typ,data=self.events.get_nowait()
    if typ=='metrics':
     self.metrics_inflight=False; self.set_metric('cpu',f"{data['cpu']:.0f}%",'Live usage'); self.set_metric('ram',f"{data['ram'].percent:.0f}%",f"{data['ram'].available/1024**3:.1f} GB free"); self.set_metric('disk',f"{data['disk'].percent:.0f}%",f"{data['disk'].free/1024**3:.0f} GB free"); g=data['gpu']; self.set_metric('gpu',(f"{g['util']:.0f}%" if g else '—'),(f"{g['temp']:.0f}°C • {g['used']/1024:.1f}/{g['total']/1024:.1f} GB" if g else 'Unavailable'))
    elif typ=='metrics_error':
     self.metrics_inflight=False; logger.warning('Metrics unavailable: %s',data)
    elif typ=='output':
     o,t=data
     if o.winfo_exists(): o.configure(state='normal'); o.delete('1.0','end'); o.insert('end',t); o.configure(state='disabled')
    elif typ=='voice_status':
     self.voice_status.configure(text='🎤 '+data)
     if hasattr(self,'voice_info') and self.voice_info.winfo_exists(): self.voice_info.configure(text=data)
     if hasattr(self,'voice_button') and self.voice_button and self.voice_button.winfo_exists():
      busy_voice = self.voice.listening
      self.voice_button.configure(text=('■' if busy_voice else '🎤'))
    elif typ=='voice_transcript':
     if hasattr(self,'voice_transcript') and self.voice_transcript.winfo_exists():
      self.voice_transcript.configure(state='normal'); self.voice_transcript.delete('1.0','end'); self.voice_transcript.insert('end',data); self.voice_transcript.configure(state='disabled')
    elif typ=='voice_detail':
     if hasattr(self,'voice_detail') and self.voice_detail.winfo_exists(): self.voice_detail.configure(text=data)
    elif typ=='voice_diagnostics':
     if hasattr(self,'voice_transcript') and self.voice_transcript.winfo_exists():
      self.voice_transcript.configure(state='normal'); self.voice_transcript.delete('1.0','end'); self.voice_transcript.insert('end',data); self.voice_transcript.configure(state='disabled')
    elif typ=='voice_test':
     if hasattr(self,'voice_transcript') and self.voice_transcript.winfo_exists():
      self.voice_transcript.configure(state='normal'); self.voice_transcript.delete('1.0','end'); self.voice_transcript.insert('end',data); self.voice_transcript.configure(state='disabled')
     if hasattr(self,'voice_detail') and self.voice_detail.winfo_exists(): self.voice_detail.configure(text='Audio diagnostics updated below')
    elif typ=='voice_text':
     self.submit_text(data)
     if hasattr(self,'voice_button') and self.voice_button.winfo_exists():
      self.voice_button.configure(text='🎤', state='normal')
    elif typ=='voice_warning':
     self.voice_status.configure(text='🎤 '+data)
     if hasattr(self,'voice_button') and self.voice_button and self.voice_button.winfo_exists(): self.voice_button.configure(text='🎤', state='normal')
     if hasattr(self,'voice_error_label') and self.voice_error_label.winfo_exists(): self.voice_error_label.configure(text=data,fg=self.C['yellow'])
     if hasattr(self,'voice_info') and self.voice_info.winfo_exists(): self.voice_info.configure(text='Voice needs attention — see details below',fg=self.C['yellow'])
    elif typ=='voice_error':
     self.voice_status.configure(text='🎤 Voice error')
     if hasattr(self,'voice_button') and self.voice_button and self.voice_button.winfo_exists(): self.voice_button.configure(text='🎤', state='normal')
     if hasattr(self,'voice_error_label') and self.voice_error_label.winfo_exists(): self.voice_error_label.configure(text=data)
     if hasattr(self,'voice_info') and self.voice_info.winfo_exists(): self.voice_info.configure(text='Voice error — see details below')
  except queue.Empty: pass
  try:
   while True:
    source,text=self.responses.get_nowait(); self.busy=False
    if self.status.winfo_exists(): self.status.configure(text='● LOCAL',fg=self.C['green'])
    if hasattr(self,'send_btn') and self.send_btn.winfo_exists(): self.send_btn.configure(state='normal')
    kind_map={'error':'error','pc':'pc','system':'system','action_pending':'action_pending','action_result':'action_result'}
    kind=kind_map.get(source,'ai')
    who={'error':'ERROR','pc':'PC','system':'SYSTEM','action_pending':'ACTION','action_result':'RESULT'}.get(source,'AI')
    self.append(who,text,kind, feedback=(source=='ai'), feedback_id=(self.session.last_interaction_id if source=='ai' else None))
    if source=='ai':
     self.voice.speak(text)
    if self.queued_voice_text and not self.busy:
     queued=self.queued_voice_text; self.queued_voice_text=None
     self.root.after(50,lambda q=queued:self.submit_text(q))
  except queue.Empty: pass
  if not self.closing:self.root.after(75,self.poll)
 def close(self):
  self.closing=True
  try: self.voice.stop()
  except Exception: logger.exception('Voice shutdown failed')
  self.root.destroy()

def main():
 root=tk.Tk(); App(root); root.mainloop()
if __name__=='__main__': main()
