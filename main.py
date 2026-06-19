"""
API de la capa de lectura — Reto 2 RCE.

POST /analizar : recibe uno o varios archivos (PDF, imagen, video, audio) y
devuelve los hechos jurídicos estructurados. Aquí queda el hook donde se
conectará la cadena de razonamiento jurídica (régimen -> exoneración ->
perjuicio -> terceros -> memo) cuando los abogados la tengan lista.

Sin límite de 32 MB: el contenedor corre con hypercorn (HTTP/2 cleartext) y el
servicio de Cloud Run está desplegado con --use-http2, lo que elimina el tope de
tamaño de request del proxy. El cliente sube el archivo directo, sin pasos extra.

Correr en local:  hypercorn main:app --bind 0.0.0.0:8000
(uvicorn también sirve en local; solo no soporta HTTP/2 para archivos grandes)
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from ingest import normalize_many
from comprehension import extraer_hechos

app = FastAPI(title="Reto 2 RCE — Análisis de demandas")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _cargar_indice() -> None:
    """En Cloud Run (sin disco persistente) descarga el índice RAG de GCS."""
    try:
        import rag
        rag.descargar_indice()
    except Exception as e:
        print(f"[startup] Índice RAG no disponible: {e}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analizar")
async def analizar(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Recibe archivos de cualquier tamaño y devuelve los hechos estructurados."""
    tmpdir = tempfile.mkdtemp(prefix="rce_")
    saved: list[str] = []
    try:
        for uf in files:
            dest = os.path.join(tmpdir, Path(uf.filename or "archivo").name)
            with open(dest, "wb") as out:
                shutil.copyfileobj(uf.file, out)
            saved.append(dest)

        blocks = normalize_many(saved)
        hechos = extraer_hechos(blocks)

        # === Cadena de razonamiento jurídica =================================
        # régimen -> exoneración -> perjuicio -> terceros -> memo
        analisis = None
        if "error" not in hechos:
            try:
                from reasoning import construir_memo
                analisis = construir_memo(hechos)
            except Exception as e:
                analisis = {"error": f"falló el razonamiento: {e}"}
        # ====================================================================

        return {
            "ok": "error" not in hechos,
            "archivos": [Path(s).name for s in saved],
            "hechos": hechos,
            "analisis": analisis,
            "memo": (analisis or {}).get("memo_markdown") if isinstance(analisis, dict) else None,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)