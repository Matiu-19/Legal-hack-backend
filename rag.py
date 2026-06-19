"""
RAG legal — ingesta y consulta de documentos jurídicos (Reto 2 RCE).

Colección única en ChromaDB con metadata por categoría:
  jurisprudencia  — sentencias y líneas jurisprudenciales (máxima prioridad)
  ley             — códigos, leyes, decretos, tablas SOAT
  doctrina        — artículos y libros académicos
  acto_admin      — conceptos y actos administrativos

Uso rápido desde la terminal:
  python rag.py ingerir "ruta/doc.pdf" jurisprudencia
  python rag.py consultar "nexo causal actividad peligrosa" --n 6
  python rag.py listar
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import chromadb
from dotenv import load_dotenv

load_dotenv()

# --- Configuración -----------------------------------------------------------
DB_PATH      = os.path.join(os.path.dirname(__file__), ".chromadb")
COLLECTION   = "legal_docs"
CATEGORIAS   = {"jurisprudencia", "ley", "doctrina", "acto_admin"}
CHUNK_SIZE   = 1200   # caracteres por chunk
CHUNK_OVERLAP = 150   # solapamiento entre chunks
EMBED_MODEL  = "gemini-embedding-001"
EMBED_BATCH  = 50     # textos por llamada a la API de embeddings


# --- Embeddings con Gemini (independiente de ChromaDB) -----------------------
_gemini_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai as _genai
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Falta GEMINI_API_KEY en el entorno.")
        _gemini_client = _genai.Client(api_key=api_key)
    return _gemini_client


def _embed(texts: list[str]) -> list[list[float]]:
    """Genera embeddings en lotes. Devuelve lista de vectores float."""
    client = _get_gemini()
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        response = client.models.embed_content(model=EMBED_MODEL, contents=batch)
        all_vecs.extend(e.values for e in response.embeddings)
    return all_vecs


# --- Singleton de colección (sin embedding function — la gestionamos nosotros)
_col: chromadb.Collection | None = None


def _get_col() -> chromadb.Collection:
    global _col
    if _col is None:
        client = chromadb.PersistentClient(path=DB_PATH)
        _col = client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _col


# --- Extracción de texto -----------------------------------------------------
def _extraer_paginas(path: str) -> list[tuple[int, str]]:
    """Devuelve [(num_pagina, texto)] para cada página con contenido."""
    doc = fitz.open(path)
    paginas: list[tuple[int, str]] = []
    for i, page in enumerate(doc):
        texto = page.get_text("text").strip()
        if len(texto) > 40:          # ignorar páginas casi vacías
            paginas.append((i + 1, texto))
    doc.close()
    return paginas


def _chunkear(texto: str) -> list[str]:
    """Divide texto en chunks solapados."""
    chunks: list[str] = []
    start = 0
    while start < len(texto):
        chunk = texto[start : start + CHUNK_SIZE]
        if len(chunk) >= 80:
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# --- API pública -------------------------------------------------------------
def ingerir(path: str, categoria: str) -> int:
    """
    Indexa un PDF en el RAG.
    Devuelve el número de chunks añadidos/actualizados.
    """
    if categoria not in CATEGORIAS:
        raise ValueError(f"Categoría '{categoria}' no válida. Opciones: {sorted(CATEGORIAS)}")

    col    = _get_col()
    nombre = Path(path).name
    paginas = _extraer_paginas(path)

    if not paginas:
        print(f"[rag] {nombre}: sin texto digital extraíble (¿PDF escaneado sin OCR?).")
        return 0

    ids: list[str]       = []
    docs: list[str]      = []
    metas: list[dict]    = []

    for pagina, texto in paginas:
        for chunk in _chunkear(texto):
            uid = hashlib.md5(f"{nombre}:{pagina}:{chunk[:60]}".encode()).hexdigest()
            ids.append(uid)
            docs.append(chunk)
            metas.append({"categoria": categoria, "fuente": nombre, "pagina": pagina})

    if ids:
        embeddings = _embed(docs)
        col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
    print(f"[rag] {nombre} ({categoria}): {len(ids)} chunks indexados.")
    return len(ids)


def ingerir_directorio(directorio: str, categoria: str) -> int:
    """Indexa todos los PDFs de un directorio con la misma categoría."""
    total = 0
    for pdf in sorted(Path(directorio).glob("*.pdf")):
        total += ingerir(str(pdf), categoria)
    return total


def consultar(
    query: str,
    n: int = 5,
    categoria: str | None = None,
) -> list[dict[str, Any]]:
    """
    Recupera los n chunks más relevantes para la consulta.

    Si se especifica `categoria`, filtra solo esa colección.
    Devuelve lista de dicts con: texto, fuente, pagina, categoria, relevancia.
    """
    col   = _get_col()
    where = {"categoria": categoria} if categoria else None

    q_emb = _embed([query])
    resultados = col.query(
        query_embeddings=q_emb,
        n_results=n,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks: list[dict[str, Any]] = []
    for doc, meta, dist in zip(
        resultados["documents"][0],
        resultados["metadatas"][0],
        resultados["distances"][0],
    ):
        chunks.append({
            "texto":      doc,
            "fuente":     meta["fuente"],
            "pagina":     meta["pagina"],
            "categoria":  meta["categoria"],
            "relevancia": round(1 - dist, 4),   # cosine similarity: 1 = idéntico
        })

    # Jurisprudencia primero (prioridad pedida por el equipo legal)
    chunks.sort(key=lambda c: (c["categoria"] != "jurisprudencia", -c["relevancia"]))
    return chunks


def consultar_multi(
    query: str,
    n_por_categoria: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """
    Consulta todas las categorías por separado y devuelve resultados agrupados.
    Útil para el módulo de razonamiento jurídico.
    """
    return {
        cat: consultar(query, n=n_por_categoria, categoria=cat)
        for cat in sorted(CATEGORIAS)
    }


def listar() -> dict[str, int]:
    """Devuelve cuántos chunks hay por categoría."""
    col = _get_col()
    todos = col.get(include=["metadatas"])
    conteo: dict[str, int] = {c: 0 for c in CATEGORIAS}
    for meta in todos["metadatas"]:
        cat = meta.get("categoria", "?")
        conteo[cat] = conteo.get(cat, 0) + 1
    return conteo


# --- CLI simple --------------------------------------------------------------
if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(description="RAG legal — CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_ing = sub.add_parser("ingerir", help="Indexar un PDF o directorio")
    p_ing.add_argument("ruta",      help="Ruta al PDF o directorio")
    p_ing.add_argument("categoria", choices=sorted(CATEGORIAS))

    p_con = sub.add_parser("consultar", help="Buscar en el índice")
    p_con.add_argument("query")
    p_con.add_argument("--n",        type=int, default=5)
    p_con.add_argument("--cat",      default=None, choices=sorted(CATEGORIAS))
    p_con.add_argument("--multi",    action="store_true",
                       help="Consultar todas las categorías por separado")

    sub.add_parser("listar", help="Ver chunks indexados por categoría")

    args = parser.parse_args()

    if args.cmd == "ingerir":
        ruta = Path(args.ruta)
        if ruta.is_dir():
            total = ingerir_directorio(str(ruta), args.categoria)
        else:
            total = ingerir(str(ruta), args.categoria)
        print(f"Total: {total} chunks.")

    elif args.cmd == "consultar":
        if args.multi:
            res = consultar_multi(args.query, n_por_categoria=args.n)
        else:
            res = consultar(args.query, n=args.n, categoria=args.cat)
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.cmd == "listar":
        print(json.dumps(listar(), ensure_ascii=False, indent=2))

    else:
        parser.print_help()