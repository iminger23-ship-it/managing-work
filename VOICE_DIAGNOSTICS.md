# Voice Diagnostics

## Python packages
Imports `sounddevice`, `numpy`, `faster_whisper`, and `pyttsx3`.

## Audio backend
Enumerates actual PortAudio input devices. Device names are displayed only when Python can query them.

## TTS
Initializes the configured Windows pyttsx3 backend and counts available voices.

## faster-whisper
Verifies that the Python package imports. The first actual transcription may still download/load the selected model.

## CUDA
Reports CTranslate2 CUDA device detection. A CUDA failure during model loading falls back to CPU/int8.

## Microphone test
Queries the selected/default input device and reports its channel count and default sample rate.

For full exception details, inspect `logs/mylocalai.log`.
