"""
Indexador batch del corpus jurídico (offline, se corre UNA vez).

Recorre un directorio raíz con subcarpetas, clasifica cada subcarpeta en una
categoría del RAG según su nombre, e indexa todos los PDFs (digitales y
escaneados con OCR) en ChromaDB. Al final puede subir el índice a GCS.

Estructura esperada (los nombres pueden variar, se clasifican por palabras clave):
  corpus/
    conceptos-actos administrativos/   -> acto_admin
    doctrina/                          -> doctrina
    jurisprudencia/                    -> jurisprudencia
    leyes/                             -> ley
    TODO SOBRE DAÑO EMERGENTE.../      -> perjuicios

Uso:
  python index_corpus.py "C:\\ruta\\corpus"              # indexa
  python index_corpus.py "C:\\ruta\\corpus" --reset      # borra y reconstruye
  python index_corpus.py "C:\\ruta\\corpus" --reset --subir   # + sube a GCS
  python index_corpus.py "C:\\ruta\\corpus" --solo jurisprudencia   # una categoría

Requisitos:
  - Credenciales para embeddings (Vertex AI):  gcloud auth application-default login
    y  $env:GOOGLE_CLOUD_PROJECT="gen-lang-client-0398885476"
  - Tesseract instalado (para OCR de escaneados).
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import rag


# --- Clasificación de subcarpeta -> categoría --------------------------------
def _norm(s: str) -> str:
    """minúsculas sin acentos, para emparejar nombres de carpeta."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower()


# orden importa: la primera coincidencia gana
_REGLAS = [
    ("jurisprudencia", ["jurisprudencia", "sentencia", "providencia", "fallo", "csj"]),
    ("perjuicios",     ["dano", "perjuicio", "lucro", "emergente", "moral", "vida en relacion", "salud"]),
    ("acto_admin",     ["concepto", "acto administrativo", "actos administrativos", "administrativ"]),
    ("doctrina",       ["doctrina", "autor", "libro", "articulo"]),
    ("ley",            ["ley", "leyes", "codigo", "decreto", "norma", "estatuto"]),
]


def clasificar(nombre_carpeta: str) -> str | None:
    n = _norm(nombre_carpeta)
    for categoria, claves in _REGLAS:
        if any(clave in n for clave in claves):
            return categoria
    return None


# --- Job ---------------------------------------------------------------------
def indexar_corpus(root: str, solo: str | None = None) -> dict[str, int]:
    root_path = Path(root)
    if not root_path.is_dir():
        raise SystemExit(f"No es un directorio: {root}")

    subcarpetas = [d for d in sorted(root_path.iterdir()) if d.is_dir()]
    if not subcarpetas:
        raise SystemExit(f"No hay subcarpetas en {root}.")

    resumen: dict[str, int] = {}
    for carpeta in subcarpetas:
        categoria = clasificar(carpeta.name)
        if categoria is None:
            print(f"\n[SKIP] '{carpeta.name}' — no se pudo clasificar (renómbrala o usa --solo).")
            continue
        if solo and categoria != solo:
            continue

        pdfs = sorted(carpeta.rglob("*.pdf"))
        print(f"\n=== {carpeta.name}  ->  [{categoria}]  ({len(pdfs)} PDFs) ===")
        nuevos = 0
        for k, pdf in enumerate(pdfs, 1):
            print(f"  [{k}/{len(pdfs)}] {pdf.name}")
            try:
                nuevos += rag.ingerir(str(pdf), categoria, ocr=True)
            except Exception as e:
                print(f"      ERROR en {pdf.name}: {e}")
        resumen[categoria] = resumen.get(categoria, 0) + nuevos

    return resumen


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexador batch del corpus jurídico")
    parser.add_argument("root", help="Directorio raíz con las subcarpetas del corpus")
    parser.add_argument("--reset", action="store_true",
                        help="Borra el índice antes de empezar (úsalo en la primera carga completa)")
    parser.add_argument("--solo", default=None, choices=sorted(rag.CATEGORIAS),
                        help="Indexar solo una categoría")
    parser.add_argument("--subir", action="store_true",
                        help="Subir el índice a GCS al terminar")
    args = parser.parse_args()

    if args.reset:
        print("[reset] Borrando índice anterior...")
        rag.reset_coleccion()

    resumen = indexar_corpus(args.root, solo=args.solo)

    print("\n========== RESUMEN ==========")
    print("Chunks nuevos por categoría en esta corrida:")
    for cat, n in resumen.items():
        print(f"  {cat}: {n}")
    print("\nTotal acumulado en el índice:")
    for cat, n in rag.listar().items():
        print(f"  {cat}: {n}")

    if args.subir:
        print()
        rag.subir_indice()


if __name__ == "__main__":
    main()
