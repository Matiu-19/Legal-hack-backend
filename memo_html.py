"""
Renderizado HTML premium del memorando (plantilla Hurtado Gandini).

Genera un HTML autocontenido (CSS inline, logo SVG, fuentes de Google) a partir
de la salida de reasoning.construir_memo(...). Pensado para mostrarse en el
navegador y descargarse con Ctrl+P -> Guardar como PDF (fidelidad perfecta).

  render_memo_html(analisis, hechos) -> str   # memorando completo
  render_ficha_html(hechos)          -> str   # ficha del caso (mismo diseño)
"""
from __future__ import annotations

import html
from typing import Any

# --- Logo SVG exacto de la spec ---------------------------------------------
_LOGO = """<svg width="200" height="72" viewBox="0 0 220 80" xmlns="http://www.w3.org/2000/svg">
  <rect x="0"  y="0"  width="13" height="52" fill="#1A1A1A"/>
  <rect x="27" y="0"  width="13" height="52" fill="#1A1A1A"/>
  <rect x="0"  y="21" width="40" height="10" fill="#1A1A1A"/>
  <rect x="0"  y="22" width="40" height="7"  fill="#C0001E"/>
  <rect x="54" y="0"  width="13" height="52" fill="#1A1A1A"/>
  <rect x="54" y="0"  width="52" height="12" fill="#1A1A1A"/>
  <rect x="54" y="40" width="52" height="12" fill="#1A1A1A"/>
  <rect x="93" y="26" width="13" height="26" fill="#1A1A1A"/>
  <rect x="66" y="26" width="40" height="7"  fill="#C0001E"/>
  <text x="0" y="72" font-family="'Source Sans 3','Helvetica Neue',Arial,sans-serif"
        font-size="13" font-weight="400" letter-spacing="2" fill="#1A1A1A">HURTADO GANDINI</text>
</svg>"""

# --- CSS de la spec ----------------------------------------------------------
_CSS = """@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=Source+Sans+3:ital,wght@0,300;0,400;0,600;0,700;1,400&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
@page { size: Letter; margin: 15mm 16mm; }
:root {
  --n: #1A1A1A; --c: #2D2D2D; --go: #4A4A4A; --gm: #7A7A7A;
  --gc: #F4F4F4; --gb: #E0E0E0; --w: #FFFFFF; --r: #C0001E;
  --fd: 'Playfair Display', Georgia, serif;
  --fs: 'Source Sans 3', 'Helvetica Neue', Arial, sans-serif;
}
body { font-family: var(--fs); font-size: 12px; line-height: 1.75; color: var(--go); background: var(--w); }
.memo { max-width: 780px; margin: 0 auto; padding: 36px 48px 32px; }
.hdr { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 28px; }
.hdr-et { font-size: 9px; color: var(--gm); letter-spacing: .8px; text-transform: uppercase; align-self: flex-end; }
.title-w { text-align: center; margin-bottom: 24px; }
.tit { font-family: var(--fd); font-size: 24px; font-weight: 600; color: var(--n); line-height: 1.3; margin-bottom: 14px; }
.bor { display: inline-block; background: var(--gc); color: var(--go); font-size: 10px; font-style: italic; padding: 9px 18px; border-radius: 3px; max-width: 600px; text-align: left; line-height: 1.6; }
.meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 28px; background: var(--gc); padding: 16px 20px; border-radius: 3px; margin-bottom: 36px; }
.ml { display: block; font-size: 8.5px; color: var(--gm); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 2px; }
.mv { display: block; font-size: 11.5px; color: var(--c); }
.sec { margin-bottom: 36px; }
.shdr { display: flex; align-items: baseline; gap: 9px; margin-bottom: 12px; }
.sn { font-size: 10px; font-weight: 700; color: var(--r); letter-spacing: 1px; min-width: 18px; }
.st { font-family: var(--fs); font-size: 13.5px; font-weight: 600; color: var(--n); }
.sec p { font-size: 12px; color: var(--go); line-height: 1.75; margin-bottom: 8px; }
.card { background: var(--gc); padding: 18px 22px; border-radius: 3px; display: flex; flex-direction: column; gap: 13px; }
.nat-banner { background: var(--r); color: #fff; padding: 11px 18px; border-radius: 3px; margin-bottom: 12px; }
.nat-banner b { font-size: 13px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; }
.nat-banner span { display: block; font-size: 10px; margin-top: 3px; font-style: italic; opacity: .92; }
.cl { display: block; font-size: 8.5px; color: var(--gm); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 3px; }
.cv { font-size: 11.5px; color: var(--c); margin: 0; }
.inv { display: flex; flex-direction: column; gap: 5px; }
.prow { display: flex; gap: 8px; align-items: flex-start; padding: 7px 11px; background: var(--gc); border-radius: 2px; }
.pid { font-size: 9px; font-weight: 700; color: var(--r); min-width: 40px; }
.pcat { font-size: 9px; color: var(--gm); min-width: 90px; font-style: italic; }
.pdesc { font-size: 10.5px; color: var(--go); line-height: 1.5; flex: 1; }
.pbadge { font-size: 8px; padding: 1px 5px; border-radius: 2px; white-space: nowrap; align-self: flex-start; margin-top: 1px; background: var(--gb); color: var(--c); }
.alerta-doc { background: #fdf8f8; border-left: 3px solid var(--r); border-radius: 0; padding: 10px 14px; margin-top: 8px; }
.alerta-doc strong { color: var(--r); font-size: 8.5px; text-transform: uppercase; letter-spacing: .5px; display: block; margin-bottom: 3px; }
.alerta-doc p { font-size: 10.5px; color: var(--go); margin: 0; line-height: 1.6; }
.mat { display: flex; flex-direction: column; gap: 5px; }
.mrow { padding: 8px 12px; border-radius: 2px; background: var(--gc); }
.melem { font-size: 8.5px; font-weight: 700; color: var(--r); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 2px; display: block; }
.mhecho { font-size: 11px; color: var(--n); font-weight: 600; margin: 0 0 2px; }
.mdet { font-size: 10px; color: var(--go); margin: 0; }
.mest { font-size: 9px; font-style: italic; color: var(--gm); margin-top: 2px; }
.args { display: flex; flex-direction: column; gap: 16px; }
.arg { padding: 18px 22px; border-radius: 2px; border-left: 3px solid var(--gb); background: #F9F9F9; }
.arg-p { background: var(--w); border-left: 3px solid var(--r); }
.ameta { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; flex-wrap: wrap; }
.anum { font-size: 11px; font-weight: 700; color: var(--r); min-width: 20px; }
.acls { font-size: 8.5px; font-weight: 700; letter-spacing: .8px; padding: 2px 7px; border-radius: 2px; text-transform: uppercase; }
.cls-p { background: var(--r); color: #fff; }
.cls-c { background: var(--gb); color: var(--go); }
.acal { display: flex; align-items: center; gap: 6px; margin-left: auto; }
.cal-l { font-size: 9px; color: var(--gm); }
.cal-v { font-size: 10px; font-weight: 600; color: var(--c); }
.dots { display: flex; gap: 3px; align-items: center; }
.d { display: inline-block; width: 6px; height: 6px; border-radius: 50%; }
.don { background: var(--r); }
.doff { background: var(--gb); }
.cnota { display: block; font-size: 8.5px; color: var(--gm); font-style: italic; margin-bottom: 7px; }
.at { font-family: var(--fs); font-size: 12.5px; font-weight: 600; color: var(--n); margin-bottom: 8px; line-height: 1.4; }
.adev { font-size: 11.5px; color: var(--go); line-height: 1.75; }
.afund { margin-top: 11px; }
.afl { display: block; font-size: 8.5px; text-transform: uppercase; letter-spacing: .7px; margin-bottom: 3px; color: var(--gm); }
.afp { font-size: 11px; color: var(--go); font-style: italic; margin: 0; line-height: 1.6; }
.arev { margin-top: 9px; }
.arl { display: block; font-size: 8.5px; text-transform: uppercase; letter-spacing: .7px; margin-bottom: 3px; color: var(--r); }
.arp { font-size: 11px; color: var(--go); margin: 0; line-height: 1.6; }
.pasos { list-style: none; display: flex; flex-direction: column; gap: 10px; }
.paso { display: flex; align-items: flex-start; gap: 10px; }
.pn { font-size: 10px; font-weight: 600; color: var(--gm); min-width: 16px; padding-top: 1px; }
.pt { font-size: 11.5px; color: var(--go); line-height: 1.65; margin: 0; }
.pm { display: block; font-size: 10px; color: var(--gm); font-style: italic; margin-top: 2px; }
.adv-blq { background: var(--gc); padding: 18px 22px; border-radius: 3px; display: flex; flex-direction: column; gap: 14px; }
.ai { display: flex; flex-direction: column; gap: 5px; }
.abg { display: flex; gap: 6px; align-items: center; }
.badge { font-size: 8.5px; font-weight: 700; letter-spacing: .7px; text-transform: uppercase; padding: 2px 7px; border-radius: 2px; }
.bt { background: var(--gb); color: var(--c); }
.b-critica { background: var(--r); color: #fff; }
.b-alta { background: var(--c); color: #fff; }
.b-media { background: var(--gb); color: var(--go); }
.b-baja { background: var(--gc); color: var(--gm); border: 1px solid var(--gb); }
.at2 { font-size: 11.5px; color: var(--go); line-height: 1.65; margin: 0; }
.acc { font-size: 11px; color: var(--go); margin: 0; }
.ind { background: var(--gc); padding: 16px 20px; border-radius: 3px; }
.ind-lbl { display: block; font-size: 8.5px; color: var(--gm); text-transform: uppercase; letter-spacing: .7px; margin-bottom: 4px; }
.ind-val { font-size: 20px; font-weight: 700; color: var(--n); }
.ind-nivel { font-size: 11px; color: var(--r); font-weight: 600; margin-left: 8px; }
.ind-p { font-size: 10.5px; color: var(--go); margin-top: 8px; line-height: 1.6; font-style: italic; }
.col2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.col2-t { font-size: 9px; font-weight: 700; color: var(--n); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 5px; }
.col2 ul { list-style: none; display: flex; flex-direction: column; gap: 5px; }
.col2 li { font-size: 10.5px; color: var(--go); padding-left: 11px; position: relative; line-height: 1.5; }
.col2 li::before { content: '·'; position: absolute; left: 0; color: var(--r); }
.conc { background: var(--gc); padding: 14px 18px; border-radius: 3px; margin-top: 12px; }
.conc-rec { font-size: 9px; font-weight: 700; color: var(--r); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 5px; display: block; }
.conc p { font-size: 11.5px; color: var(--go); line-height: 1.65; margin: 0; }
.fg { margin-bottom: 16px; }
.fc { display: block; font-size: 8.5px; color: var(--gm); text-transform: uppercase; letter-spacing: .7px; margin-bottom: 6px; }
.fl { list-style: none; display: flex; flex-direction: column; gap: 4px; }
.fi { font-size: 11px; color: var(--go); padding-left: 12px; position: relative; line-height: 1.55; }
.fi::before { content: '–'; position: absolute; left: 0; color: var(--gb); }
.foot { display: flex; align-items: center; justify-content: space-between; margin-top: 40px; padding-top: 14px; font-size: 9.5px; color: var(--gm); }
.ffirma { font-weight: 700; }
.ffirma::after { content: '·'; color: var(--r); margin: 0 5px; }
.fav { font-style: italic; }
@media print {
  body { padding: 0; max-width: 100%; }
  /* en PDF, los márgenes los da @page: el .memo va a ancho completo sin padding */
  .memo { max-width: 100%; padding: 0; margin: 0; }
  /* no partir unidades atómicas a mitad de página */
  .card, .arg, .mrow, .prow, .ai, .paso, .fg, .alerta-doc, .ind, .conc, .meta { break-inside: avoid; }
  /* el título de sección no se queda solo al final de una página */
  .shdr, .st { break-after: avoid; }
}"""


# --- helpers -----------------------------------------------------------------
def esc(x: Any) -> str:
    return html.escape(str(x)) if x is not None else ""


def _expand_citas(labels: list[str], fuentes: dict[str, Any]) -> str:
    vistas, out = set(), []
    for lab in labels or []:
        info = (fuentes or {}).get(lab)
        if info:
            key = (info.get("fuente"), info.get("pagina"))
            if key in vistas:
                continue
            vistas.add(key)
            out.append(f"{info.get('fuente')}, p.{info.get('pagina')}")
    return "; ".join(out)


def _dots(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    return ("".join('<span class="d don"></span>' for _ in range(n))
            + "".join('<span class="d doff"></span>' for _ in range(5 - n)))


# SÓLIDO/PROBABLE/DÉBIL -> (clasificación, calificación) — la spec prohíbe esas etiquetas.
_MAP_SOLIDEZ = {"SOLIDO": ("PRINCIPAL", 5), "PROBABLE": ("COMPLEMENTARIO", 3), "DEBIL": ("COMPLEMENTARIO", 2)}


def _seccion(n: int, titulo: str, cuerpo: str) -> str:
    return (f'<section class="sec"><div class="shdr"><span class="sn">{n:02d}</span>'
            f'<h2 class="st">{esc(titulo)}</h2></div>{cuerpo}</section>')


# --- secciones del memorando -------------------------------------------------
def _meta_html(hechos: dict, analisis: dict) -> str:
    partes = (hechos or {}).get("partes") or {}
    dem = ", ".join(partes.get("demandantes") or []) or None
    dda = ", ".join(partes.get("demandados_potenciales") or []) or None
    tc = (hechos or {}).get("tipo_caso") or {}
    cat = tc.get("categoria") if isinstance(tc, dict) else tc
    riesgo = ((analisis or {}).get("riesgo") or {}).get("indice_riesgo_condena")
    campos = [("Demandante(s)", dem), ("Demandado(s)", dda),
              ("Tipo de caso", cat), ("Riesgo de condena (preliminar)", riesgo)]
    celdas = "".join(f'<div><span class="ml">{esc(l)}</span><span class="mv">{esc(v)}</span></div>'
                     for l, v in campos if v)
    return f'<div class="meta">{celdas}</div>' if celdas else ""


def _inventario_html(prob: dict) -> str:
    if not prob or "error" in prob:
        return ""
    rows = []
    for p in prob.get("inventario") or []:
        badge = f"{esc(p.get('calidad_lectura',''))} · {esc(p.get('confianza',''))}"
        rows.append(
            f'<div class="prow"><span class="pid">{esc(p.get("id",""))}</span>'
            f'<span class="pcat">{esc(p.get("tipo",""))}</span>'
            f'<span class="pdesc">{esc(p.get("descripcion",""))}</span>'
            f'<span class="pbadge">{badge}</span></div>'
        )
    for d in prob.get("documentos_ilegibles") or []:
        rows.append(
            f'<div class="alerta-doc"><strong>Alerta — {esc(d.get("archivo",""))}</strong>'
            f'<p>{esc(d.get("problema",""))} {esc(d.get("recomendacion",""))}</p></div>'
        )
    return f'<div class="inv">{"".join(rows)}</div>' if rows else ""


_ELEM_LABEL = {"hecho_imputable": "Hecho imputable", "dano": "Daño", "nexo": "Nexo causal",
               "nexo_causal": "Nexo causal", "cuantificacion": "Cuantificación", "exoneracion": "Exoneración"}


def _matriz_html(prob: dict) -> str:
    if not prob or "error" in prob:
        return ""
    rows = []
    for m in prob.get("matriz_hecho_prueba") or []:
        elem = _ELEM_LABEL.get(m.get("elemento", ""), m.get("elemento", ""))
        sop = m.get("prueba_soporta") or "—"
        con = m.get("prueba_contradice") or "—"
        rows.append(
            f'<div class="mrow"><span class="melem">{esc(elem)}</span>'
            f'<p class="mhecho">{esc(m.get("aspecto",""))}</p>'
            f'<p class="mdet">Soporta: {esc(sop)} · Contradice: {esc(con)}</p>'
            f'<p class="mest">{esc(m.get("estado",""))} · riesgo {esc(m.get("riesgo_demandado",""))}</p></div>'
        )
    return f'<div class="mat">{"".join(rows)}</div>' if rows else ""


def _regimen_html(reg: dict, fuentes: dict) -> str:
    if not reg or "error" in reg:
        return ""
    nombre = reg.get("etiqueta_legible") or reg.get("regimen") or "—"
    _NAT = {"subjetiva_culpa_probada": "subjetiva", "subjetiva_culpa_presunta": "subjetiva",
            "medica": "subjetiva", "actividad_peligrosa": "objetiva",
            "objetiva_actividad_peligrosa": "objetiva", "objetiva_producto": "objetiva",
            "seguro_soat": "objetiva"}
    nat = (reg.get("naturaleza") or _NAT.get(reg.get("regimen", ""), "")).upper()
    banner = ""
    if nat:
        expl = reg.get("naturaleza_explicacion") or ""
        banner = (f'<div class="nat-banner"><b>Responsabilidad {esc(nat)}</b>'
                  + (f'<span>{esc(expl)}</span>' if expl else "") + "</div>")
    filas = [("Régimen aplicable", nombre), ("Nivel de confianza", reg.get("nivel_confianza")),
             ("Carga de la prueba", reg.get("carga_de_la_prueba"))]
    if reg.get("diligencia_exonera") is not None:
        filas.append(("¿La diligencia exonera?", "Sí" if reg.get("diligencia_exonera") else "No (solo causa extraña)"))
    if reg.get("clasificacion_contestable") and reg.get("regimen_alternativo"):
        filas.append(("Régimen alternativo (contestable)", reg.get("regimen_alternativo")))
    filas.append(("Consecuencia probatoria", reg.get("consecuencia_probatoria")))
    cuerpo = "".join(f'<div><span class="cl">{esc(l)}</span><p class="cv">{esc(v)}</p></div>'
                     for l, v in filas if v)
    labels = list(reg.get("citas", []))
    for fj in reg.get("fundamento_juridico", []):
        labels += fj.get("citas", [])
    cit = _expand_citas(labels, fuentes)
    if cit:
        cuerpo += f'<div><span class="cl">Fundamento jurídico</span><p class="cv afp">{esc(cit)}</p></div>'
    return f'{banner}<div class="card">{cuerpo}</div>'


def _elementos_html(el: dict, fuentes: dict) -> str:
    if not el or "error" in el:
        return ""
    bloques = []
    d = el.get("dano") or {}
    if d:
        bloques.append(("Daño", f"certeza {esc(d.get('certeza',''))}", d.get("analisis"),
                        d.get("ataque_defensa"), d.get("citas")))
    n = el.get("nexo_causal") or {}
    if n:
        bloques.append(("Nexo causal", f"fuerza {esc(n.get('fuerza',''))} · interrupción: {esc(n.get('interrupcion','ninguna'))}",
                        n.get("analisis"), n.get("puntos_debiles"), n.get("citas")))
    im = el.get("imputacion") or {}
    if im:
        bloques.append(("Imputación / factor de atribución", f"{esc(im.get('factor',''))} · fuerza {esc(im.get('fuerza',''))}",
                        im.get("analisis"), im.get("defensa"), im.get("citas")))
    cards = []
    for titulo, sub, analisis, ataque, citas in bloques:
        partes = [f'<div><span class="cl">{esc(titulo)} — {sub}</span>'
                  f'<p class="cv">{esc(analisis)}</p></div>']
        if ataque:
            partes.append(f'<div><span class="cl">Ataque / defensa</span><p class="cv afp">{esc(ataque)}</p></div>')
        cit = _expand_citas(citas, fuentes)
        if cit:
            partes.append(f'<div><span class="cl">Fundamento</span><p class="cv afp">{esc(cit)}</p></div>')
        cards.append(f'<div class="card">{"".join(partes)}</div>')
    return '<div style="display:flex;flex-direction:column;gap:12px">' + "".join(cards) + "</div>"


def _exoneracion_html(ex: dict, fuentes: dict) -> str:
    if not ex or "error" in ex:
        return ""
    cards = []
    for c in ex.get("causales_exoneracion") or []:
        partes = [f'<div><span class="cl">{esc(c.get("causal",""))} — viabilidad {esc(c.get("viabilidad",""))}</span>'
                  f'<p class="cv">{esc(c.get("fundamento_factico",""))}</p></div>']
        if c.get("que_probar"):
            partes.append(f'<div><span class="cl">Qué debe probar la defensa</span><p class="cv">{esc(c["que_probar"])}</p></div>')
        cit = _expand_citas(c.get("citas"), fuentes)
        if cit:
            partes.append(f'<div><span class="cl">Fundamento</span><p class="cv afp">{esc(cit)}</p></div>')
        cards.append(f'<div class="card">{"".join(partes)}</div>')
    return '<div style="display:flex;flex-direction:column;gap:12px">' + "".join(cards) + "</div>" if cards else ""


def _perjuicio_html(pj: dict, fuentes: dict) -> str:
    if not pj or "error" in pj:
        return ""
    cards = []
    for r in pj.get("rubros") or []:
        extra = f" · reclamado: {esc(r.get('monto_reclamado'))}" if r.get("monto_reclamado") else ""
        partes = [f'<div><span class="cl">{esc(r.get("rubro",""))} — soportado: {esc(r.get("soportado",""))}{extra}</span></div>']
        for lab, key in (("Estándar probatorio", "estandar_probatorio"), ("Deficiencia", "deficiencia"),
                         ("Ataque", "ataque"), ("Herramienta procesal", "herramienta_procesal")):
            if r.get(key):
                partes.append(f'<div><span class="cl">{lab}</span><p class="cv">{esc(r[key])}</p></div>')
        pdd = r.get("pruebas_de_descargo") or []
        if pdd:
            partes.append(f'<div><span class="cl">Pruebas de descargo</span><p class="cv">{esc("; ".join(pdd))}</p></div>')
        cit = _expand_citas(r.get("citas"), fuentes)
        if cit:
            partes.append(f'<div><span class="cl">Fundamento</span><p class="cv afp">{esc(cit)}</p></div>')
        cards.append(f'<div class="card">{"".join(partes)}</div>')
    if pj.get("objecion_juramento_estimatorio"):
        cards.append(f'<div class="card"><div><span class="cl">Objeción al juramento estimatorio (art. 206 CGP)</span>'
                     f'<p class="cv">{esc(pj["objecion_juramento_estimatorio"])}</p></div></div>')
    return '<div style="display:flex;flex-direction:column;gap:12px">' + "".join(cards) + "</div>" if cards else ""


def _terceros_html(t: dict, fuentes: dict) -> str:
    if not t or "error" in t:
        return ""
    cards = []
    for v in t.get("vinculaciones") or []:
        partes = [f'<div><span class="cl">{esc(v.get("tipo",""))} → {esc(v.get("destinatario",""))} — viabilidad {esc(v.get("viabilidad",""))}</span>'
                  f'<p class="cv">{esc(v.get("justificacion",""))}</p></div>']
        if v.get("requisitos"):
            partes.append(f'<div><span class="cl">Requisitos</span><p class="cv">{esc(v["requisitos"])}</p></div>')
        cit = _expand_citas(v.get("citas"), fuentes)
        if cit:
            partes.append(f'<div><span class="cl">Fundamento</span><p class="cv afp">{esc(cit)}</p></div>')
        cards.append(f'<div class="card">{"".join(partes)}</div>')
    return '<div style="display:flex;flex-direction:column;gap:12px">' + "".join(cards) + "</div>" if cards else ""


def _argumentos_html(memo: dict, fuentes: dict) -> str:
    args = []
    for a in memo.get("argumentos") or []:
        clas, cal = _MAP_SOLIDEZ.get(str(a.get("solidez", "DEBIL")).upper(), ("COMPLEMENTARIO", 2))
        args.append({**a, "_clas": clas, "_cal": cal})
    args.sort(key=lambda a: (a["_clas"] != "PRINCIPAL", -a["_cal"]))
    if not args:
        return ""
    out = []
    for i, a in enumerate(args, 1):
        es_p = a["_clas"] == "PRINCIPAL"
        cls_cls = "cls-p" if es_p else "cls-c"
        arg_cls = "arg arg-p" if es_p else "arg"
        rev = ""
        if a.get("requiere_revision_abogado"):
            rev = (f'<div class="arev"><span class="arl">Requiere revisión por abogado</span>'
                   f'<p class="arp">{esc(a["requiere_revision_abogado"])}</p></div>')
        cit = _expand_citas(a.get("citas"), fuentes)
        fund = (f'<div class="afund"><span class="afl">Fundamento</span><p class="afp">{esc(cit)}</p></div>'
                if cit else "")
        out.append(
            f'<div class="{arg_cls}"><div class="ameta"><span class="anum">{i:02d}</span>'
            f'<span class="acls {cls_cls}">{"Principal" if es_p else "Complementario"}</span>'
            f'<div class="acal"><span class="cal-l">Calificación estratégica</span>'
            f'<span class="cal-v">{a["_cal"]}/5</span><div class="dots">{_dots(a["_cal"])}</div></div></div>'
            f'<span class="cnota">Valoración estratégica interna — sujeta a revisión jurídica.</span>'
            f'<h3 class="at">{esc(a.get("tesis",""))}</h3>'
            f'<p class="adev">{esc(a.get("desarrollo",""))}</p>{fund}{rev}</div>'
        )
    return f'<div class="args">{"".join(out)}</div>'


def _riesgo_html(riesgo: dict) -> str:
    if not riesgo or "error" in riesgo:
        return ""
    raw_val = str(riesgo.get("indice_riesgo_condena", "") or "")
    nivel = str(riesgo.get("nivel", "") or "")
    # el valor suele venir como "60% - alto": separar el % del nivel para no duplicar
    if " - " in raw_val:
        cabeza, _, cola = raw_val.partition(" - ")
        raw_val = cabeza.strip()
        nivel = nivel or cola.strip()
    val, nivel = esc(raw_val), esc(nivel)
    h = (f'<div class="ind"><span class="ind-lbl">Índice preliminar jurídico-probatorio de riesgo de condena</span>'
         f'<div><span class="ind-val">{val}</span><span class="ind-nivel">{nivel}</span></div>'
         f'<p class="ind-p">Este índice no sustituye el criterio del abogado ni predice la decisión judicial. '
         f'Es un índice preliminar autónomo calculado con base en la calidad de los hechos, pruebas y fuentes disponibles.</p></div>')
    pf = riesgo.get("puntos_fuertes_defensa") or []
    pd = riesgo.get("puntos_debiles_defensa") or []
    if pf or pd:
        li_f = "".join(f"<li>{esc(x)}</li>" for x in pf)
        li_d = "".join(f"<li>{esc(x)}</li>" for x in pd)
        h += (f'<div class="col2"><div><p class="col2-t">Puntos fuertes de la defensa</p><ul>{li_f}</ul></div>'
              f'<div><p class="col2-t">Puntos débiles de la defensa</p><ul>{li_d}</ul></div></div>')
    con = riesgo.get("conciliacion") or {}
    if con:
        rango = f" — rango: {esc(con.get('rango_sugerido'))}" if con.get("rango_sugerido") else ""
        h += (f'<div class="conc"><span class="conc-rec">Recomendación: {esc(con.get("recomendacion",""))}{rango}</span>'
              f'<p>{esc(con.get("justificacion",""))}</p></div>')
    return h


_PRIO_ORD = {"alta": 0, "media": 1, "baja": 2}


def _pruebas_ad_html(pa: dict) -> str:
    if not pa or "error" in pa:
        return ""
    ps = sorted(pa.get("pruebas") or [], key=lambda x: _PRIO_ORD.get(str(x.get("prioridad", "baja")).lower(), 3))
    if not ps:
        return ""
    items = []
    for p in ps:
        items.append(
            f'<li class="paso"><span class="pn">{esc(str(p.get("prioridad","")).upper()[:1])}</span>'
            f'<div><p class="pt"><strong>{esc(p.get("prueba",""))}</strong> ({esc(p.get("tipo",""))}) — {esc(p.get("razon",""))}</p>'
            f'<span class="pm">{esc(p.get("hecho_objetivo",""))} · {esc(p.get("elemento",""))}</span></div></li>'
        )
    return f'<ol class="pasos">{"".join(items)}</ol>'


def _pasos_html(memo: dict) -> str:
    ps = memo.get("siguientes_pasos") or []
    if not ps:
        return ""
    items = "".join(f'<li class="paso"><span class="pn">{i}.</span><div><p class="pt">{esc(p)}</p></div></li>'
                    for i, p in enumerate(ps, 1))
    return f'<ol class="pasos">{items}</ol>'


def _advertencias_html(memo: dict) -> str:
    advs = memo.get("advertencias") or []
    if not advs:
        return ""
    items = "".join(f'<div class="ai"><div class="abg"><span class="badge bt">Revisión</span>'
                    f'<span class="badge b-media">Media</span></div><p class="at2">{esc(a)}</p></div>'
                    for a in advs)
    return f'<div class="adv-blq">{items}</div>'


def _fuentes_html(fuentes: dict, prob: dict) -> str:
    grupos = {"Jurisprudencia": [], "Legislación": [], "Doctrina": [], "Pruebas del expediente": []}
    vistas = set()
    for info in (fuentes or {}).values():
        key = (info.get("fuente"), info.get("pagina"))
        if key in vistas:
            continue
        vistas.add(key)
        cat = info.get("categoria")
        txt = f"{info.get('fuente')}, p.{info.get('pagina')}"
        if cat in ("jurisprudencia", "perjuicios"):
            grupos["Jurisprudencia"].append(txt)
        elif cat in ("ley", "acto_admin"):
            grupos["Legislación"].append(txt)
        elif cat == "doctrina":
            grupos["Doctrina"].append(txt)
    for p in (prob or {}).get("inventario") or []:
        grupos["Pruebas del expediente"].append(f'{p.get("id","")}: {p.get("descripcion","")}')
    out = []
    for nombre, items in grupos.items():
        if items:
            li = "".join(f'<li class="fi">{esc(x)}</li>' for x in items)
            out.append(f'<div class="fg"><span class="fc">{esc(nombre)}</span><ul class="fl">{li}</ul></div>')
    return "".join(out)


def _shell(titulo: str, cuerpo: str) -> str:
    return (f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">'
            f'<title>{esc(titulo)} — Hurtado Gandini</title><style>{_CSS}</style></head>'
            f'<body><div class="memo">'
            f'<header class="hdr"><div>{_LOGO}</div>'
            f'<span class="hdr-et">Documento de análisis jurídico</span></header>'
            f'{cuerpo}'
            f'<footer class="foot"><div><span class="ffirma">Hurtado Gandini</span>'
            f'<span>{esc(titulo)}</span></div>'
            f'<span class="fav">Documento de uso interno. Sujeto a validación jurídica.</span></footer>'
            f'</div></body></html>')


# --- API pública -------------------------------------------------------------
def render_memo_html(analisis: dict[str, Any], hechos: dict[str, Any] | None = None) -> str:
    analisis = analisis or {}
    hechos = hechos or {}
    fuentes = analisis.get("fuentes") or {}
    memo = analisis.get("memo") or {}

    titulo = "Memorando de estrategia defensiva"
    aviso = ("Borrador generado con apoyo tecnológico. Requiere validación de un abogado antes de "
             "cualquier actuación procesal. Las citas y fuentes deben verificarse en los repositorios oficiales.")
    head = (f'<div class="title-w"><h1 class="tit">{esc(titulo)}</h1>'
            f'<div class="bor">{esc(aviso)}</div></div>{_meta_html(hechos, analisis)}')

    # (constructor, título) en el orden de la spec
    candidatos = [
        (memo.get("sintesis_estrategia") and f'<p>{esc(memo["sintesis_estrategia"])}</p>', "Síntesis estratégica"),
        (_inventario_html(analisis.get("probatorio") or {}), "Inventario de pruebas"),
        (_matriz_html(analisis.get("probatorio") or {}), "Matriz hecho – prueba – elemento jurídico"),
        (_regimen_html(analisis.get("regimen") or {}, fuentes), "Régimen de responsabilidad"),
        (_elementos_html(analisis.get("elementos") or {}, fuentes), "Elementos de la responsabilidad"),
        (_exoneracion_html(analisis.get("exoneracion") or {}, fuentes), "Causales de exoneración"),
        (_perjuicio_html(analisis.get("perjuicio") or {}, fuentes), "Cuestionamiento del perjuicio"),
        (_terceros_html(analisis.get("terceros") or {}, fuentes), "Vinculación de terceros"),
        (_argumentos_html(memo, fuentes), "Argumentos defensivos"),
        (_riesgo_html(analisis.get("riesgo") or {}), "Índice preliminar de riesgo y conciliación"),
        (_pruebas_ad_html(analisis.get("pruebas_adicionales") or {}), "Pruebas adicionales recomendadas"),
        (_pasos_html(memo), "Siguientes pasos"),
        (_advertencias_html(memo), "Advertencias de revisión humana"),
        (_fuentes_html(fuentes, analisis.get("probatorio") or {}), "Fuentes citadas"),
    ]
    secciones, n = [], 1
    for cuerpo, tit in candidatos:
        if cuerpo:
            secciones.append(_seccion(n, tit, cuerpo))
            n += 1
    return _shell(titulo, head + "".join(secciones))


def render_ficha_html(hechos: dict[str, Any]) -> str:
    hechos = hechos or {}
    titulo = "Ficha del caso"
    head = (f'<div class="title-w"><h1 class="tit">{esc(titulo)}</h1>'
            f'<div class="bor">Resumen estructurado de los hechos. Documento preliminar de apoyo; '
            f'requiere validación de un abogado.</div></div>{_meta_html(hechos, {})}')

    secciones, n = [], 1

    def add(cuerpo, tit):
        nonlocal n
        if cuerpo:
            secciones.append(_seccion(n, tit, cuerpo))
            n += 1

    if hechos.get("resumen_factico"):
        add(f'<p>{esc(hechos["resumen_factico"])}</p>', "Resumen fáctico")

    lista = hechos.get("hechos") or []
    if lista:
        con = [h for h in lista if h.get("fecha")]
        sin = [h for h in lista if not h.get("fecha")]
        con.sort(key=lambda h: str(h.get("fecha")))
        filas = "".join(
            f'<div class="mrow"><span class="melem">{esc(h.get("fecha") or "s.f.")}</span>'
            f'<p class="mdet">{esc(h.get("hecho",""))}</p></div>' for h in con + sin)
        add(f'<div class="mat">{filas}</div>', "Línea de tiempo de los hechos")

    danos = hechos.get("danos_alegados") or []
    if danos:
        cuerpo = "".join(f'<div class="prow"><span class="pdesc"><strong>{esc(d.get("tipo",""))}:</strong> '
                         f'{esc(d.get("descripcion",""))}</span></div>' for d in danos)
        add(f'<div class="inv">{cuerpo}</div>', "Daños alegados")

    pruebas = hechos.get("pruebas_aportadas") or []
    if pruebas:
        cuerpo = "".join(f'<div class="prow"><span class="pcat">{esc(p.get("tipo",""))}</span>'
                         f'<span class="pdesc">{esc(p.get("descripcion",""))}</span></div>' for p in pruebas)
        add(f'<div class="inv">{cuerpo}</div>', "Pruebas aportadas")

    cuantia = hechos.get("cuantia") or {}
    if cuantia.get("monto_total") or cuantia.get("rubros"):
        filas = []
        if cuantia.get("monto_total"):
            filas.append(f'<div><span class="cl">Monto total</span><p class="cv">{esc(cuantia["monto_total"])}</p></div>')
        for r in cuantia.get("rubros") or []:
            filas.append(f'<div><span class="cl">{esc(r.get("rubro",""))}</span>'
                         f'<p class="cv">{esc(r.get("monto") or "s/d")} — {esc(r.get("soporte") or "sin soporte")}</p></div>')
        add(f'<div class="card">{"".join(filas)}</div>', "Cuantía")

    vacios = hechos.get("vacios_o_dudas") or []
    if vacios:
        li = "".join(f'<li class="fi">{esc(v)}</li>' for v in vacios)
        add(f'<ul class="fl">{li}</ul>', "Vacíos, dudas o datos pendientes")

    return _shell(titulo, head + "".join(secciones))