"""
Generación de PDFs descargables — Reto 2 RCE.

Dos documentos:
  pdf_hechos(hechos)   -> "Ficha del caso" (croquis estructurado: partes,
                          línea de tiempo, lugar/vehículos, daños, pruebas).
  pdf_memo(analisis)   -> Memorando de estrategia defensiva (desde el markdown).

Usa fpdf2 (puro Python). Las fuentes core son latin-1, así que se sanea el texto
(acentos del español sí son latin-1; se reemplazan guiones largos, comillas
tipográficas, flechas y emojis).
"""
from __future__ import annotations

from typing import Any

from fpdf import FPDF

# --- Saneo de texto a latin-1 ------------------------------------------------
_REEMPLAZOS = {
    "—": "-", "–": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...",
    "→": "->", "•": "-", "·": "-", "✅": "[OK]", "⚠️": "[!]", "⚠": "[!]",
    "🎯": "", "📊": "", "🌙": "", "🔴": "[R]", "🟢": "[V]", "🟡": "[A]", "⚫": "[G]",
}


def _san(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    for k, v in _REEMPLAZOS.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


# --- Paleta ------------------------------------------------------------------
AZUL = (31, 58, 95)
GRIS = (90, 99, 112)
GRIS_CLARO = (236, 239, 243)
ROJO = (155, 40, 50)
VERDE = (30, 100, 60)


class _PDF(FPDF):
    titulo_doc = "Documento"

    def header(self) -> None:
        self.set_fill_color(*AZUL)
        self.rect(0, 0, self.w, 18, "F")
        self.set_y(5)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(255, 255, 255)
        self.cell(0, 8, _san(self.titulo_doc), align="L")
        self.set_y(20)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*GRIS)
        self.multi_cell(0, 3.5,
                        _san("Borrador generado por IA como apoyo. Preliminar; requiere "
                             "validacion de un abogado. Cada cita debe verificarse en la "
                             "fuente oficial."), align="L")
        self.set_y(-8)
        self.cell(0, 4, f"Pagina {self.page_no()}", align="R")


# --- Helpers de maquetado ----------------------------------------------------
def _h2(pdf: _PDF, texto: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*AZUL)
    pdf.multi_cell(0, 7, _san(texto))
    pdf.set_draw_color(*AZUL)
    pdf.set_line_width(0.4)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(2)
    pdf.set_text_color(0, 0, 0)


def _h3(pdf: _PDF, texto: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*AZUL)
    pdf.multi_cell(0, 5.5, _san(texto))
    pdf.set_text_color(0, 0, 0)


def _parrafo(pdf: _PDF, texto: str, size: float = 10) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", size)
    pdf.multi_cell(0, 5, _san(texto), markdown=True)


def _bullet(pdf: _PDF, texto: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(5, 5, "-")
    pdf.set_x(pdf.l_margin + 5)
    pdf.multi_cell(0, 5, _san(texto), markdown=True)


def _campo(pdf: _PDF, etiqueta: str, valor: str) -> None:
    if not valor:
        return
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*GRIS)
    pdf.multi_cell(0, 5, _san(etiqueta))
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _san(valor))
    pdf.ln(1)


# --- PDF 1: Ficha del caso (croquis) -----------------------------------------
def pdf_hechos(hechos: dict[str, Any]) -> bytes:
    pdf = _PDF()
    pdf.titulo_doc = "Ficha del caso - Hechos"
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    tc = hechos.get("tipo_caso") or {}
    categoria = tc.get("categoria") if isinstance(tc, dict) else tc
    if categoria:
        pdf.set_fill_color(*GRIS_CLARO)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*AZUL)
        pdf.cell(0, 8, _san(f"  Tipo de caso:  {str(categoria).upper()}"), fill=True)
        pdf.ln(10)
        pdf.set_text_color(0, 0, 0)

    # Partes enfrentadas (croquis de confrontación)
    partes = hechos.get("partes") or {}
    dem = partes.get("demandantes") or []
    dda = partes.get("demandados_potenciales") or []
    if dem or dda:
        _h2(pdf, "Partes")
        col_w = (pdf.w - pdf.l_margin - pdf.r_margin - 10) / 2
        y0 = pdf.get_y()
        x0 = pdf.l_margin
        # Demandantes
        pdf.set_xy(x0, y0)
        pdf.set_fill_color(*GRIS_CLARO)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(col_w, 6, _san("DEMANDANTE(S)"), fill=True, align="C")
        # Demandados
        pdf.set_xy(x0 + col_w + 10, y0)
        pdf.cell(col_w, 6, _san("DEMANDADO(S)"), fill=True, align="C")
        # contenidos
        pdf.set_xy(x0, y0 + 7)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(col_w, 5, _san("\n".join(f"- {d}" for d in dem) or "-"))
        y_izq = pdf.get_y()
        pdf.set_xy(x0 + col_w + 10, y0 + 7)
        pdf.multi_cell(col_w, 5, _san("\n".join(f"- {d}" for d in dda) or "-"))
        y_fin = max(y_izq, pdf.get_y())
        # "vs" en el medio (antes de bajar el cursor)
        pdf.set_xy(x0 + col_w, y0 + 7)
        pdf.set_font("Helvetica", "BI", 9)
        pdf.set_text_color(*ROJO)
        pdf.cell(10, 5, "vs", align="C")
        pdf.set_text_color(0, 0, 0)
        # bajar el cursor por debajo de ambas columnas
        pdf.set_y(y_fin + 3)

    if hechos.get("resumen_factico"):
        _h2(pdf, "Resumen factico")
        _parrafo(pdf, hechos["resumen_factico"])

    # Línea de tiempo de hechos
    lista = hechos.get("hechos") or []
    if lista:
        _h2(pdf, "Linea de tiempo de los hechos")
        con_fecha = [h for h in lista if h.get("fecha")]
        sin_fecha = [h for h in lista if not h.get("fecha")]
        con_fecha.sort(key=lambda h: str(h.get("fecha")))
        for h in con_fecha + sin_fecha:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*AZUL)
            f = h.get("fecha") or "s.f."
            x = pdf.get_x()
            pdf.cell(26, 5, _san(str(f)))
            pdf.set_x(x + 26)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.multi_cell(0, 5, _san(h.get("hecho", "")))
            pdf.ln(0.5)

    # Daños alegados
    danos = hechos.get("danos_alegados") or []
    if danos:
        _h2(pdf, "Danos alegados")
        for d in danos:
            _bullet(pdf, f"**{d.get('tipo','')}**: {d.get('descripcion','')}")

    # Pruebas
    pruebas = hechos.get("pruebas_aportadas") or []
    if pruebas:
        _h2(pdf, "Pruebas aportadas")
        for p in pruebas:
            _bullet(pdf, f"**{p.get('tipo','')}**: {p.get('descripcion','')}")

    # Cuantía
    cuantia = hechos.get("cuantia") or {}
    if cuantia.get("monto_total") or cuantia.get("rubros"):
        _h2(pdf, "Cuantia")
        if cuantia.get("monto_total"):
            _campo(pdf, "Monto total:", str(cuantia["monto_total"]))
        for r in cuantia.get("rubros", []):
            _bullet(pdf, f"**{r.get('rubro','')}**: {r.get('monto') or 's/d'} "
                         f"({r.get('soporte') or 'sin soporte'})")

    # Vacíos / dudas
    vacios = hechos.get("vacios_o_dudas") or []
    if vacios:
        _h2(pdf, "Vacios, dudas o datos pendientes")
        for v in vacios:
            _bullet(pdf, str(v))

    return bytes(pdf.output())


# --- PDF 2: Memorando (desde markdown) ---------------------------------------
def pdf_memo(analisis: dict[str, Any]) -> bytes:
    md = (analisis or {}).get("memo_markdown") or ""
    pdf = _PDF()
    pdf.titulo_doc = "Memorando de estrategia defensiva"
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    for raw in md.split("\n"):
        linea = raw.rstrip()
        pdf.set_x(pdf.l_margin)          # cada línea arranca en el margen izquierdo
        if not linea.strip():
            pdf.ln(2)
            continue
        if linea.startswith("# "):
            continue  # el título ya está en el header
        if linea.startswith("### "):
            _h3(pdf, linea[4:])
        elif linea.startswith("## "):
            _h2(pdf, linea[3:])
        elif linea.startswith("> "):
            pdf.set_font("Helvetica", "I", 8.5)
            pdf.set_text_color(*GRIS)
            pdf.multi_cell(0, 4.5, _san(linea[2:]))
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        elif linea.lstrip().startswith("- "):
            _bullet(pdf, linea.lstrip()[2:])
        else:
            _parrafo(pdf, linea)

    return bytes(pdf.output())