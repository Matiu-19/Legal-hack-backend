"""
Capa de lectura — ingesta multimodal (Reto 2 RCE).

`normalize(path)` convierte cualquier archivo de entrada (PDF digital o
escaneado, imagen, video o audio) en una lista de bloques de contenido que la
API de Claude entiende de forma nativa:

- PDF (digital o escaneado): bloque `document` en base64. Claude lo OCR-ea
  internamente; NO se necesita un motor de OCR externo (Tesseract, etc.).
- Imagen: bloque `image` en base64.
- Video: se extraen frames con ffmpeg (muestreados y limitados) como bloques
  `image`, y el audio se transcribe (opcional) a un bloque `text`.
- Audio: se transcribe a un bloque `text`.

Cada archivo se antecede con un marcador de fuente, para que después cada
hecho extraído pueda rastrearse al documento del que salió (trazabilidad).
"""
from __future__ import annotations

import base64
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# --- Parámetros ajustables -------------------------------------------------
MAX_VIDEO_FRAMES = 16          # tope de frames por video (controla tokens/costo)
MAX_FPS = 2.0                  # no muestrear más denso que esto

IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


# --- Helpers ----------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("utf-8")


def guess_kind(path: str) -> str:
    """Devuelve 'pdf' | 'image' | 'video' | 'audio' | 'unknown'."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_MEDIA_TYPES:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    mt, _ = mimetypes.guess_type(path)
    if mt == "application/pdf":
        return "pdf"
    if mt and mt.startswith("image/"):
        return "image"
    if mt and mt.startswith("video/"):
        return "video"
    if mt and mt.startswith("audio/"):
        return "audio"
    return "unknown"


def _source_marker(name: str) -> dict[str, Any]:
    return {"type": "text", "text": f"\n=== Fuente: {name} ===\n"}


# --- Bloques por tipo -------------------------------------------------------
PDF_DPI         = 120   # resolución de render para páginas escaneadas
TEXT_MIN_CHARS  = 100   # una página con menos texto que esto se trata como escaneada
MAX_TEXT_PAGES  = 60    # tope de páginas digitales (texto = barato, se permiten muchas)
MAX_IMAGE_PAGES = 25    # tope de páginas escaneadas (imagen = caro en tokens)


def _muestrear(indices: list[int], tope: int) -> set[int]:
    """Muestreo uniforme de una lista de índices si supera el tope."""
    if len(indices) <= tope:
        return set(indices)
    step = len(indices) / tope
    return {indices[int(k * step)] for k in range(tope)}


def pdf_blocks(path: str) -> list[dict[str, Any]]:
    """
    Convierte un PDF en bloques, optimizando costo/precisión:
    - Página con capa de texto  -> bloque de TEXTO (barato, sin ruido de OCR).
    - Página escaneada (sin texto) -> bloque de IMAGEN (Gemini la lee nativo).
    Mantiene el orden del documento y aplica topes separados por tipo.
    """
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    n = len(doc)

    # Clasificar cada página: digital (con texto) vs escaneada.
    texto_por_pagina: dict[int, str] = {}
    paginas_escaneadas: list[int] = []
    for i in range(n):
        t = doc[i].get_text("text").strip()
        if len(t) >= TEXT_MIN_CHARS:
            texto_por_pagina[i] = t
        else:
            paginas_escaneadas.append(i)

    keep_texto = _muestrear(sorted(texto_por_pagina), MAX_TEXT_PAGES)
    keep_imagen = _muestrear(paginas_escaneadas, MAX_IMAGE_PAGES)

    blocks: list[dict[str, Any]] = []
    mat = fitz.Matrix(PDF_DPI / 72, PDF_DPI / 72)
    for i in range(n):
        if i in keep_texto:
            blocks.append({"type": "text",
                           "text": f"[Página {i + 1}]\n{texto_por_pagina[i]}"})
        elif i in keep_imagen:
            pix = doc[i].get_pixmap(matrix=mat, alpha=False)
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _b64(pix.tobytes("png")),
                },
            })
    doc.close()
    return blocks


def image_block(path: str, media_type: str | None = None) -> dict[str, Any]:
    ext = Path(path).suffix.lower()
    media_type = media_type or IMAGE_MEDIA_TYPES.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": _b64(f.read()),
            },
        }


# --- ffmpeg (solo para video/audio) ----------------------------------------
# En Windows, winget instala ffmpeg pero el PATH del proceso puede no incluirlo
# si el servidor arrancó antes de la instalación. Recargamos el PATH del sistema.
def _reload_win_path() -> None:
    import winreg
    paths = []
    for hive, scope in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER,  r"Environment"),
    ):
        try:
            with winreg.OpenKey(hive, scope) as k:
                val, _ = winreg.QueryValueEx(k, "PATH")
                paths.append(val)
        except FileNotFoundError:
            pass
    if paths:
        os.environ["PATH"] = os.pathsep.join(paths) + os.pathsep + os.environ.get("PATH", "")

if os.name == "nt":
    _reload_win_path()


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg no está instalado. Es necesario para video/audio "
            "(macOS: brew install ffmpeg · Ubuntu: sudo apt install ffmpeg)."
        )


def _video_duration(path: str) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    p = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ])
    try:
        return float(p.stdout.strip())
    except (ValueError, AttributeError):
        return None


def _extract_frames(path: str, out_dir: str, max_frames: int) -> list[str]:
    _ensure_ffmpeg()
    duration = _video_duration(path)
    if duration and duration > 0:
        fps = min(max_frames / duration, MAX_FPS)
        fps = max(fps, 0.05)
    else:
        fps = 0.5
    pattern = os.path.join(out_dir, "frame_%04d.jpg")
    p = _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
        "-vf", f"fps={fps}", "-q:v", "3", pattern,
    ])
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg falló extrayendo frames: {p.stderr.strip()}")
    frames = sorted(Path(out_dir).glob("frame_*.jpg"))
    if len(frames) > max_frames:                       # muestreo uniforme
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]
    return [str(f) for f in frames]


def _extract_audio(path: str, out_dir: str) -> str | None:
    _ensure_ffmpeg()
    wav = os.path.join(out_dir, "audio.wav")
    p = _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
        "-vn", "-ac", "1", "-ar", "16000", wav,
    ])
    if p.returncode != 0 or not os.path.exists(wav):
        return None
    return wav


def _transcribe_media(media_path: str) -> str | None:
    """Extrae el audio y lo transcribe (si faster-whisper está instalado)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = _extract_audio(media_path, tmp)
        if not wav:
            return None
        try:
            from transcribe import transcribe
            return transcribe(wav)
        except Exception:
            return None


def video_blocks(path: str, max_frames: int = MAX_VIDEO_FRAMES) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for fr in _extract_frames(path, tmp, max_frames):
            blocks.append(image_block(fr, media_type="image/jpeg"))
    text = _transcribe_media(path)
    if text:
        blocks.append({"type": "text",
                       "text": f"[Transcripción del audio del video]\n{text}"})
    return blocks


# --- API pública del módulo -------------------------------------------------
def normalize(path: str) -> list[dict[str, Any]]:
    """Convierte un archivo en una lista de bloques de contenido para Claude."""
    name = Path(path).name
    kind = guess_kind(path)
    blocks: list[dict[str, Any]] = [_source_marker(name)]

    if kind == "pdf":
        blocks.extend(pdf_blocks(path))
    elif kind == "image":
        blocks.append(image_block(path))
    elif kind == "video":
        blocks.extend(video_blocks(path))
    elif kind == "audio":
        text = _transcribe_media(path)
        if text:
            blocks.append({"type": "text",
                           "text": f"[Transcripción de audio: {name}]\n{text}"})
        else:
            blocks.append({"type": "text",
                           "text": f"[Audio sin transcripción: {name}. "
                                   f"Instala faster-whisper para habilitarla.]"})
    else:
        blocks.append({"type": "text", "text": f"[Archivo no soportado: {name}]"})

    return blocks


def normalize_many(paths: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in paths:
        out.extend(normalize(p))
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python ingest.py <archivo> [<archivo> ...]")
        raise SystemExit(1)
    bloques = normalize_many(sys.argv[1:])
    tipos: dict[str, int] = {}
    for b in bloques:
        tipos[b["type"]] = tipos.get(b["type"], 0) + 1
    print(f"{len(bloques)} bloques generados: {tipos}")
