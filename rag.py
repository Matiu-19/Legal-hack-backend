"""
RAG legal — ingesta y consulta de documentos jurídicos (Reto 2 RCE).

Colección única en ChromaDB con metadata por categoría:
  jurisprudencia  — sentencias y líneas jurisprudenciales (máxima prioridad)
  ley             — códigos, leyes, decretos, tablas SOAT
  doctrina        — artículos y libros académicos
  acto_admin      — conceptos y actos administrativos
  perjuicios      — daño emergente, lucro cesante, daño moral, daño a la vida
                    de relación y daño a la salud (insumo para atacar la cuantía)

PDFs digitales: texto nativo con PyMuPDF.
PDFs escaneados: OCR local con Tesseract (idioma español).

Uso rápido desde la terminal:
  python rag.py ingerir "ruta/doc.pdf" jurisprudencia
  python rag.py consultar "nexo causal actividad peligrosa" --n 6
  python rag.py listar
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import time
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import chromadb
from dotenv import load_dotenv

load_dotenv()

# Consola de Windows en cp1252 no puede imprimir ciertos caracteres (acentos
# combinados en nombres de archivo) y tumba el proceso. Forzar UTF-8 tolerante.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- Configuración -----------------------------------------------------------
DB_PATH       = os.path.join(os.path.dirname(__file__), ".chromadb")
COLLECTION    = "legal_docs"
CATEGORIAS    = {"jurisprudencia", "ley", "doctrina", "acto_admin", "perjuicios"}
CHUNK_SIZE    = 1200   # caracteres por chunk
CHUNK_OVERLAP = 150    # solapamiento entre chunks
EMBED_MODEL   = "gemini-embedding-001"
EMBED_DIM     = 768    # dims reducidas (Matryoshka): índice ~4x más pequeño, casi igual calidad
EMBED_BATCH   = 50     # textos por llamada (se reduce solo si la API lo rechaza)
OCR_LANG      = "spa"  # idioma Tesseract
OCR_DPI       = 220    # resolución de render para OCR
MIN_TEXT_LEN  = 40     # menos de esto en una página → se intenta OCR
# Carpeta con spa.traineddata (no se pudo escribir en Program Files sin admin)
TESSDATA_DIR  = os.path.join(os.path.dirname(__file__), "tessdata")

GCS_BUCKET       = os.environ.get("GCS_BUCKET", "apphack-rce-docs")
GCS_INDEX_OBJECT = os.environ.get("GCS_INDEX_OBJECT", "chromadb-index.tar.gz")


# --- Tesseract (configuración de ruta en Windows) ----------------------------
def _config_tesseract() -> None:
    import pytesseract
    if os.name == "nt":
        for cand in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ):
            if os.path.exists(cand):
                pytesseract.pytesseract.tesseract_cmd = cand
                break
    # Usar la carpeta local de tessdata (donde dejamos spa.traineddata)
    if os.path.isdir(TESSDATA_DIR):
        os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR


# --- Embeddings con Gemini (independiente de ChromaDB) -----------------------
_gemini_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai as _genai
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
        if project:
            _gemini_client = _genai.Client(vertexai=True, project=project, location="us-central1")
        elif api_key:
            _gemini_client = _genai.Client(api_key=api_key)
        else:
            raise RuntimeError("Define GOOGLE_CLOUD_PROJECT (Vertex AI) o GEMINI_API_KEY (AI Studio).")
    return _gemini_client


def _normalize(v: list[float]) -> list[float]:
    """Normaliza a norma 1 (recomendado al reducir dimensiones)."""
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v] if n else v


def _embed_batch(client, batch: list[str]) -> list[list[float]]:
    from google.genai import types
    resp = client.models.embed_content(
        model=EMBED_MODEL,
        contents=batch,
        config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM),
    )
    return [_normalize(e.values) for e in resp.embeddings]


def _embed(texts: list[str]) -> list[list[float]]:
    """
    Embeddings en lotes, robusto:
    - reintenta con backoff ante errores transitorios (rate limit, red),
    - reduce el tamaño de lote si la API lo rechaza por tamaño.
    """
    client = _get_gemini()
    all_vecs: list[list[float]] = []
    i = 0
    batch_size = EMBED_BATCH
    while i < len(texts):
        batch = texts[i : i + batch_size]
        try:
            all_vecs.extend(_embed_batch(client, batch))
            i += batch_size
        except Exception as e:
            msg = str(e).lower()
            # Lote demasiado grande para el modelo → reducir y reintentar
            if batch_size > 1 and ("invalid" in msg or "batch" in msg or "size" in msg or "400" in msg):
                batch_size = max(1, batch_size // 4)
                continue
            # Rate limit / transitorio → backoff y reintento (hasta agotar)
            if "429" in msg or "resource" in msg or "deadline" in msg or "unavailable" in msg or "503" in msg:
                time.sleep(5)
                continue
            raise
    return all_vecs


# --- Singleton de colección --------------------------------------------------
import threading

_col: chromadb.Collection | None = None
_index_lock = threading.Lock()
_index_listo = False


def asegurar_indice() -> None:
    """
    Garantiza que el índice esté en disco antes de abrirlo. En Cloud Run (sin
    disco persistente) lo descarga de GCS una sola vez, protegido por lock para
    que peticiones concurrentes no disparen descargas en paralelo. En local, si
    ya existe .chromadb, no hace nada.
    """
    global _index_listo
    if _index_listo or os.path.isdir(DB_PATH):
        _index_listo = True
        return
    with _index_lock:
        if _index_listo or os.path.isdir(DB_PATH):
            _index_listo = True
            return
        descargar_indice()
        _index_listo = True


def _get_col() -> chromadb.Collection:
    global _col
    if _col is None:
        asegurar_indice()
        client = chromadb.PersistentClient(path=DB_PATH)
        _col = client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _col


def reset_coleccion() -> None:
    """Borra y recrea la colección (necesario si cambian las dimensiones)."""
    global _col
    client = chromadb.PersistentClient(path=DB_PATH)
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    _col = client.get_or_create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})


# --- Extracción de texto (con OCR fallback) ----------------------------------
def _ocr_page(page) -> str:
    import pytesseract
    from PIL import Image
    mat = fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    try:
        return pytesseract.image_to_string(img, lang=OCR_LANG)
    except pytesseract.TesseractError:
        # Si falta el idioma español, cae a OCR por defecto
        return pytesseract.image_to_string(img)


def _extraer_paginas(path: str, ocr: bool = True) -> tuple[list[tuple[int, str]], int]:
    """
    Devuelve ([(num_pagina, texto)], paginas_ocr).
    Usa texto digital; si una página casi no tiene texto y ocr=True, la OCR-ea.
    """
    if ocr:
        _config_tesseract()
    doc = fitz.open(path)
    paginas: list[tuple[int, str]] = []
    ocr_count = 0
    for i, page in enumerate(doc):
        texto = page.get_text("text").strip()
        if len(texto) < MIN_TEXT_LEN and ocr:
            ocr_texto = _ocr_page(page).strip()
            if len(ocr_texto) > len(texto):
                texto = ocr_texto
                ocr_count += 1
        if len(texto) > MIN_TEXT_LEN:
            paginas.append((i + 1, texto))
    doc.close()
    return paginas, ocr_count


def _chunkear(texto: str) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(texto):
        chunk = texto[start : start + CHUNK_SIZE]
        if len(chunk) >= 80:
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# --- API pública -------------------------------------------------------------
def ingerir(path: str, categoria: str, ocr: bool = True) -> int:
    """Indexa un PDF en el RAG. Devuelve el número de chunks añadidos."""
    if categoria not in CATEGORIAS:
        raise ValueError(f"Categoría '{categoria}' no válida. Opciones: {sorted(CATEGORIAS)}")

    col = _get_col()
    nombre = Path(path).name

    # Skip a nivel de archivo: si ya está indexado, no re-extraer (evita re-OCR
    # en re-corridas). Un archivo se inserta entero o nada, así que es seguro.
    try:
        ya = col.get(where={"$and": [{"fuente": nombre}, {"categoria": categoria}]}, limit=1)
        if ya["ids"]:
            print(f"[rag] {nombre} ({categoria}): ya indexado, se omite.")
            return 0
    except Exception:
        pass

    paginas, ocr_count = _extraer_paginas(path, ocr=ocr)
    if not paginas:
        print(f"[rag] {nombre}: sin texto extraíble (ni con OCR).")
        return 0

    # IDs únicos con contador (evita colisión de md5 si el texto se repite).
    ids, docs, metas = [], [], []
    vistos: set[str] = set()
    idx = 0
    for pagina, texto in paginas:
        for chunk in _chunkear(texto):
            uid = hashlib.md5(f"{nombre}:{pagina}:{idx}:{chunk[:40]}".encode()).hexdigest()
            idx += 1
            if uid in vistos:               # dedupe defensivo dentro del archivo
                continue
            vistos.add(uid)
            ids.append(uid)
            docs.append(chunk)
            metas.append({"categoria": categoria, "fuente": nombre, "pagina": pagina})

    if ids:
        embeddings = _embed(docs)
        col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)

    ocr_nota = f" (OCR en {ocr_count} págs)" if ocr_count else ""
    print(f"[rag] {nombre} ({categoria}): {len(ids)} chunks nuevos{ocr_nota}.")
    return len(ids)


def ingerir_directorio(directorio: str, categoria: str, ocr: bool = True) -> int:
    total = 0
    pdfs = sorted(Path(directorio).rglob("*.pdf"))
    for k, pdf in enumerate(pdfs, 1):
        print(f"  [{k}/{len(pdfs)}] {pdf.name}")
        total += ingerir(str(pdf), categoria, ocr=ocr)
    return total


def consultar(query: str, n: int = 5, categoria: str | None = None) -> list[dict[str, Any]]:
    """Recupera los n chunks más relevantes. Jurisprudencia se prioriza."""
    col = _get_col()
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
        resultados["documents"][0], resultados["metadatas"][0], resultados["distances"][0]
    ):
        chunks.append({
            "texto":      doc,
            "fuente":     meta["fuente"],
            "pagina":     meta["pagina"],
            "categoria":  meta["categoria"],
            "relevancia": round(1 - dist, 4),
        })
    chunks.sort(key=lambda c: (c["categoria"] != "jurisprudencia", -c["relevancia"]))
    return chunks


def consultar_multi(query: str, n_por_categoria: int = 3) -> dict[str, list[dict[str, Any]]]:
    return {cat: consultar(query, n=n_por_categoria, categoria=cat) for cat in sorted(CATEGORIAS)}


def listar() -> dict[str, int]:
    """Cuenta chunks por categoría. Pagina para no exceder el límite de variables
    de SQLite (traer ~35k metadatos de golpe revienta el backend)."""
    col = _get_col()
    conteo: dict[str, int] = {c: 0 for c in CATEGORIAS}
    total = col.count()
    paso = 5000
    for off in range(0, total, paso):
        batch = col.get(include=["metadatas"], limit=paso, offset=off)
        for meta in batch["metadatas"]:
            cat = meta.get("categoria", "?")
            conteo[cat] = conteo.get(cat, 0) + 1
    return conteo


# --- Sincronización del índice con GCS ---------------------------------------
def subir_indice() -> str:
    """Empaqueta .chromadb y lo sube a GCS. Devuelve gs://... ."""
    import tarfile, tempfile
    from google.cloud import storage

    if not os.path.isdir(DB_PATH):
        raise RuntimeError(f"No existe el índice local en {DB_PATH}.")

    tmp = os.path.join(tempfile.gettempdir(), "chromadb-index.tar.gz")
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(DB_PATH, arcname=".chromadb")

    client = storage.Client()
    blob = client.bucket(GCS_BUCKET).blob(GCS_INDEX_OBJECT)
    blob.upload_from_filename(tmp)
    os.remove(tmp)
    destino = f"gs://{GCS_BUCKET}/{GCS_INDEX_OBJECT}"
    print(f"[rag] Índice subido a {destino}")
    return destino


def descargar_indice(force: bool = False) -> bool:
    """
    Descarga el índice de GCS si no existe localmente.
    Devuelve True si quedó un índice disponible. Pensado para el arranque en
    Cloud Run (sin disco persistente).
    """
    import tarfile, tempfile
    from google.cloud import storage

    if os.path.isdir(DB_PATH) and not force:
        return True
    try:
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET).blob(GCS_INDEX_OBJECT)
        if not blob.exists():
            print(f"[rag] No hay índice en gs://{GCS_BUCKET}/{GCS_INDEX_OBJECT}.")
            return False
        tmp = os.path.join(tempfile.gettempdir(), "chromadb-index.tar.gz")
        blob.download_to_filename(tmp)
        with tarfile.open(tmp, "r:gz") as tar:
            tar.extractall(os.path.dirname(DB_PATH))
        os.remove(tmp)
        print(f"[rag] Índice descargado de GCS.")
        return True
    except Exception as e:
        print(f"[rag] No se pudo descargar el índice: {e}")
        return False


# --- CLI ---------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(description="RAG legal — CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_ing = sub.add_parser("ingerir", help="Indexar un PDF o directorio")
    p_ing.add_argument("ruta")
    p_ing.add_argument("categoria", choices=sorted(CATEGORIAS))
    p_ing.add_argument("--no-ocr", action="store_true", help="Desactivar OCR")

    p_con = sub.add_parser("consultar", help="Buscar en el índice")
    p_con.add_argument("query")
    p_con.add_argument("--n", type=int, default=5)
    p_con.add_argument("--cat", default=None, choices=sorted(CATEGORIAS))
    p_con.add_argument("--multi", action="store_true")

    sub.add_parser("listar", help="Ver chunks por categoría")
    sub.add_parser("subir", help="Subir el índice a GCS")
    sub.add_parser("descargar", help="Descargar el índice de GCS")

    args = parser.parse_args()

    if args.cmd == "ingerir":
        ruta = Path(args.ruta)
        ocr = not args.no_ocr
        if ruta.is_dir():
            total = ingerir_directorio(str(ruta), args.categoria, ocr=ocr)
        else:
            total = ingerir(str(ruta), args.categoria, ocr=ocr)
        print(f"Total: {total} chunks nuevos.")
    elif args.cmd == "consultar":
        res = (consultar_multi(args.query, n_por_categoria=args.n) if args.multi
               else consultar(args.query, n=args.n, categoria=args.cat))
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "listar":
        print(json.dumps(listar(), ensure_ascii=False, indent=2))
    elif args.cmd == "subir":
        subir_indice()
    elif args.cmd == "descargar":
        descargar_indice(force=True)
    else:
        parser.print_help()