"""
API de la capa de lectura — Reto 2 RCE.

POST /analizar : recibe uno o varios archivos (PDF, imagen, video, audio) y
devuelve los hechos jurídicos estructurados. Aquí queda el hook donde se
conectará la cadena de razonamiento jurídica (régimen -> exoneración ->
perjuicio -> terceros -> memo) cuando los abogados la tengan lista.

Correr:  uvicorn main:app --reload --port 8000
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

app = FastAPI(title="Reto 2 RCE — Capa de lectura")

# CORS abierto al front de Next.js en local.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analizar")
async def analizar(files: list[UploadFile] = File(...)) -> dict[str, Any]:
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

        # === HOOK: cadena de razonamiento jurídica ===========================
        # régimen -> exoneración -> perjuicio -> terceros -> memo
        # La lectura ya entrega `hechos` estructurado y con fuentes.
        # memo = construir_memo(hechos)
        # =====================================================================

        return {
            "ok": "error" not in hechos,
            "archivos": [Path(s).name for s in saved],
            "hechos": hechos,
            "memo": None,  # se llenará al conectar la cadena
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
