"""
Transcripción de audio (opcional).

Usa faster-whisper si está instalado. Si no lo está, `transcribe()` devuelve
None y el resto del sistema sigue funcionando (los frames del video se siguen
analizando; solo se omite el texto del audio).

Instalar para habilitar:  pip install faster-whisper
Modelo configurable con la variable de entorno WHISPER_MODEL (default: small).
"""
from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()

_MODEL = None


def _load():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from faster_whisper import WhisperModel  # import perezoso; puede no estar
    size = os.environ.get("WHISPER_MODEL", "small")
    _MODEL = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL


def transcribe(audio_path: str) -> str | None:
    try:
        model = _load()
    except Exception:
        return None  # faster-whisper no instalado -> se omite la transcripción
    try:
        segments, _info = model.transcribe(audio_path, language="es")
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or None
    except Exception:
        return None
