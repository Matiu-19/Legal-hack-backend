"""
Razonamiento jurídico — Reto 2 RCE (el núcleo que da puntos).

Toma los `hechos` que produce comprehension.extraer_hechos(...) y corre una
cadena determinista de 5 pasos. Cada paso consulta el RAG en las categorías que
más le sirven, razona con Gemini y devuelve JSON estructurado. Cada argumento
cita ÚNICAMENTE material recuperado (guardrail anti-alucinación de sentencias).

Cadena:
  1. Régimen de responsabilidad   ← jurisprudencia + doctrina + ley
  2. Nexo causal y exoneración     ← jurisprudencia + ley
  3. Cuestionamiento del perjuicio ← jurisprudencia + perjuicios
  4. Vinculación de terceros       ← jurisprudencia + ley + doctrina
  5. Memorando de estrategia       ← integra 1-4, ordenado por solidez

Para casos de producto/alimentos/médica se suma acto_admin (conceptos), y
siempre hay un barrido general que trae "lo más parecido" sin importar categoría.

RAG aumenta, no gobierna: si el índice está vacío, los pasos siguen razonando
(el régimen es lógica pura) y solo se degrada la cita.
"""
from __future__ import annotations

import json
import os
from typing import Any

import rag
from comprehension import _parse_json

REASONING_MODEL = os.environ.get("REASONING_MODEL", "gemini-2.5-flash")
# Para el memo final puedes subir calidad con gemini-2.5-pro (env MEMO_MODEL).
MEMO_MODEL = os.environ.get("MEMO_MODEL", REASONING_MODEL)

_PREFIJO = {"jurisprudencia": "J", "ley": "L", "doctrina": "D",
            "acto_admin": "A", "perjuicios": "P"}

_DISCLAIMER = (
    "BORRADOR generado por IA como apoyo. Requiere validación de un abogado "
    "antes de cualquier actuación procesal. Cada cita debe verificarse en la "
    "fuente oficial (Relatoría de la Sala Civil de la CSJ / diario oficial)."
)


# --- Recuperación con etiquetas de cita --------------------------------------
def _query_base(hechos: dict[str, Any]) -> str:
    partes = [hechos.get("resumen_factico", "")]
    tc = hechos.get("tipo_caso") or {}
    if isinstance(tc, dict) and tc.get("categoria"):
        partes.append(f"Tipo de caso: {tc['categoria']}.")
    for d in (hechos.get("danos_alegados") or [])[:5]:
        partes.append(d.get("descripcion", ""))
    return " ".join(p for p in partes if p).strip() or "responsabilidad civil extracontractual"


def _cats_extra(hechos: dict[str, Any]) -> list[str]:
    """acto_admin ayuda mucho en producto/alimentos/médica (regulación sanitaria)."""
    tc = (hechos.get("tipo_caso") or {})
    cat = tc.get("categoria", "") if isinstance(tc, dict) else ""
    texto = (hechos.get("resumen_factico", "") + " " + str(cat)).lower()
    if any(k in texto for k in ("producto", "aliment", "medic", "médic", "invima", "sanitar")):
        return ["acto_admin"]
    return []


def recuperar(query: str, prioridad: list[str], hechos: dict[str, Any],
              n_prio: int = 4, n_general: int = 4) -> list[dict[str, Any]]:
    """
    Recupera chunks de las categorías prioritarias + un barrido general, deduplica
    y asigna una etiqueta de cita ([J1], [L2], ...). Devuelve [] si no hay índice.
    """
    try:
        col = rag._get_col()
        q_emb = rag._embed([query])
    except Exception as e:
        print(f"[reasoning] RAG no disponible: {e}")
        return []

    cats = list(dict.fromkeys(prioridad + _cats_extra(hechos)))
    crudos: list[dict[str, Any]] = []

    def _añadir(res):
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            crudos.append({
                "texto": doc, "fuente": meta["fuente"], "pagina": meta["pagina"],
                "categoria": meta["categoria"], "relevancia": round(1 - dist, 4),
            })

    try:
        for cat in cats:
            r = col.query(query_embeddings=q_emb, n_results=n_prio,
                          where={"categoria": cat},
                          include=["documents", "metadatas", "distances"])
            if r["documents"] and r["documents"][0]:
                _añadir(r)
        rg = col.query(query_embeddings=q_emb, n_results=n_general,
                       include=["documents", "metadatas", "distances"])
        if rg["documents"] and rg["documents"][0]:
            _añadir(rg)
    except Exception as e:
        print(f"[reasoning] error en consulta RAG: {e}")
        return []

    # Dedup por (fuente, pagina, inicio del texto), conservando mayor relevancia
    mejor: dict[tuple, dict[str, Any]] = {}
    for c in crudos:
        k = (c["fuente"], c["pagina"], c["texto"][:80])
        if k not in mejor or c["relevancia"] > mejor[k]["relevancia"]:
            mejor[k] = c
    chunks = sorted(mejor.values(),
                    key=lambda c: (c["categoria"] != "jurisprudencia", -c["relevancia"]))

    # Etiquetas de cita
    contador: dict[str, int] = {}
    for c in chunks:
        p = _PREFIJO.get(c["categoria"], "X")
        contador[p] = contador.get(p, 0) + 1
        c["label"] = f"{p}{contador[p]}"
    return chunks


def _render_contexto(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return "(No hay material en el corpus para esta consulta. Razona con base " \
               "en la ley general, pero NO inventes citas; deja las citas vacías.)"
    bloques = []
    for c in chunks:
        bloques.append(
            f"[{c['label']}] ({c['categoria']} · {c['fuente']} · p.{c['pagina']})\n{c['texto']}"
        )
    return "\n\n".join(bloques)


def _registro_citas(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["label"]: {"fuente": c["fuente"], "pagina": c["pagina"],
                         "categoria": c["categoria"]} for c in chunks}


# --- Llamada al modelo -------------------------------------------------------
def _llm(system: str, user: str, model: str = REASONING_MODEL, max_tokens: int = 3500) -> str:
    from google.genai import types
    client = rag._get_gemini()
    resp = client.models.generate_content(
        model=model,
        contents=[user],
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            temperature=0.2,
        ),
    )
    return resp.text


_REGLA_CITAS = (
    "Cita ÚNICAMENTE con las etiquetas [X#] del MATERIAL JURÍDICO de abajo "
    "(p. ej. [J1], [L2]). NUNCA inventes números de sentencia ni de artículo. "
    "Si una afirmación no tiene respaldo en el material, márcala como sin cita y "
    "anótala como punto a verificar por el abogado. Responde SOLO con JSON válido, "
    "sin texto adicional ni backticks."
)


def _ejecutar_paso(system: str, instruccion: str, hechos: dict[str, Any],
                   chunks: list[dict[str, Any]]) -> dict[str, Any]:
    contexto = _render_contexto(chunks)
    user = (
        f"HECHOS DEL CASO (JSON):\n{json.dumps(hechos, ensure_ascii=False)}\n\n"
        f"MATERIAL JURÍDICO RECUPERADO:\n{contexto}\n\n"
        f"{instruccion}\n\n{_REGLA_CITAS}"
    )
    try:
        raw = _llm(system, user)
        return _parse_json(raw)
    except Exception as e:
        return {"error": str(e)}


# --- Paso 1: Régimen ---------------------------------------------------------
def paso1_regimen(hechos: dict[str, Any]) -> dict[str, Any]:
    q = _query_base(hechos) + " régimen de responsabilidad culpa probada presunta " \
        "actividad peligrosa artículo 2341 2356 carga de la prueba"
    chunks = recuperar(q, ["jurisprudencia", "doctrina", "ley"], hechos)
    system = (
        "Eres un abogado litigante experto en responsabilidad civil extracontractual "
        "en Colombia, del lado de la DEFENSA (demandado). Clasificas el régimen de "
        "responsabilidad y su normativa."
    )
    instruccion = (
        "Determina el RÉGIMEN de responsabilidad y devuelve este JSON:\n"
        "{\n"
        '  "regimen": "subjetiva_culpa_probada | subjetiva_culpa_presunta | '
        'objetiva_actividad_peligrosa | objetiva_producto | medica | otro",\n'
        '  "explicacion": "por qué encuadra ahí, en 2-4 frases",\n'
        '  "normativa_aplicable": [{"norma":"ej. Art. 2356 CC","citas":["J1"]}],\n'
        '  "carga_de_la_prueba": "a quién corresponde y qué implica para la defensa",\n'
        '  "diligencia_exonera": true/false,\n'
        '  "clasificacion_contestable": true/false,\n'
        '  "estrategia_reclasificacion": "si la defensa puede reencuadrar el régimen '
        'para mover la carga al demandante (p.ej. defender 2341 frente a 2356), '
        'explícalo; si no, null",\n'
        '  "citas": ["J1","L2"]\n'
        "}"
    )
    out = _ejecutar_paso(system, instruccion, hechos, chunks)
    out["_chunks"] = chunks
    return out


# --- Paso 2: Nexo causal y exoneración --------------------------------------
def paso2_exoneracion(hechos: dict[str, Any], regimen: dict[str, Any]) -> dict[str, Any]:
    q = _query_base(hechos) + " nexo causal causa extraña exoneración culpa exclusiva " \
        "de la víctima hecho de un tercero fuerza mayor caso fortuito"
    chunks = recuperar(q, ["jurisprudencia", "ley"], hechos)
    reg = regimen.get("regimen", "desconocido")
    system = (
        "Eres un abogado de la DEFENSA en RCE en Colombia. Analizas el nexo causal y "
        "la viabilidad de causales de exoneración, condicionadas al régimen."
    )
    instruccion = (
        f"El régimen determinado es: {reg}. Recuerda: en régimen OBJETIVO solo exonera "
        "la causa extraña (la diligencia NO exonera); en SUBJETIVO también sirve probar "
        "diligencia. Devuelve este JSON:\n"
        "{\n"
        '  "elementos_nexo": {"conducta":"...","dano":"...","nexo_causal":"...",'
        '"puntos_debiles_del_nexo":"qué puede atacar la defensa"},\n'
        '  "causales_exoneracion": [\n'
        '    {"causal":"culpa_exclusiva_victima | hecho_de_tercero | fuerza_mayor_caso_fortuito",\n'
        '     "viabilidad":"alta | media | baja",\n'
        '     "fundamento_factico":"hechos del caso que la soportan",\n'
        '     "que_probar":"prueba que tendría que aportar la defensa",\n'
        '     "citas":["J1"]}\n'
        "  ],\n"
        '  "citas": ["J1","L2"]\n'
        "}"
    )
    out = _ejecutar_paso(system, instruccion, hechos, chunks)
    out["_chunks"] = chunks
    return out


# --- Paso 3: Cuestionamiento del perjuicio ----------------------------------
def paso3_perjuicio(hechos: dict[str, Any]) -> dict[str, Any]:
    q = _query_base(hechos) + " cuantía perjuicio daño emergente lucro cesante daño moral " \
        "soporte probatorio topes indemnización prueba del daño"
    chunks = recuperar(q, ["jurisprudencia", "perjuicios"], hechos)
    system = (
        "Eres un abogado de la DEFENSA en RCE en Colombia. Atacas la estimación del "
        "perjuicio rubro por rubro: verificas soporte probatorio y propones pruebas de descargo."
    )
    instruccion = (
        "Analiza la CUANTÍA reclamada y, rubro por rubro, devuelve este JSON:\n"
        "{\n"
        '  "rubros": [\n'
        '    {"rubro":"dano_emergente | lucro_cesante | dano_moral | dano_vida_relacion | dano_salud | otro",\n'
        '     "monto_reclamado":"texto o null",\n'
        '     "soportado":"si | parcial | no",\n'
        '     "debilidad":"por qué el soporte es insuficiente o especulativo",\n'
        '     "ataque":"argumento concreto de la defensa",\n'
        '     "pruebas_de_descargo":["pruebas que la defensa debería solicitar"],\n'
        '     "citas":["J1","P1"]}\n'
        "  ],\n"
        '  "observacion_juramento_estimatorio":"si aplica art. 206 CGP, nota; si no, null",\n'
        '  "citas":["J1"]\n'
        "}"
    )
    out = _ejecutar_paso(system, instruccion, hechos, chunks)
    out["_chunks"] = chunks
    return out


# --- Paso 4: Vinculación de terceros ----------------------------------------
def paso4_terceros(hechos: dict[str, Any], regimen: dict[str, Any]) -> dict[str, Any]:
    q = _query_base(hechos) + " llamamiento en garantía aseguradora póliza denuncia del " \
        "pleito fabricante vinculación de terceros artículo 64 65 CGP"
    chunks = recuperar(q, ["jurisprudencia", "ley", "doctrina"], hechos)
    system = (
        "Eres un abogado de la DEFENSA en RCE en Colombia. Evalúas si conviene vincular "
        "terceros (llamamiento en garantía a aseguradora, denuncia del pleito a fabricante)."
    )
    instruccion = (
        "Evalúa la vinculación de terceros y devuelve este JSON:\n"
        "{\n"
        '  "vinculaciones": [\n'
        '    {"tipo":"llamamiento_en_garantia | denuncia_del_pleito",\n'
        '     "destinatario":"ej. aseguradora / fabricante / otro responsable",\n'
        '     "justificacion":"por qué procede según los hechos",\n'
        '     "viabilidad":"alta | media | baja",\n'
        '     "requisitos":"qué se necesita (póliza, contrato, etc.)",\n'
        '     "citas":["L1"]}\n'
        "  ],\n"
        '  "citas":["L1","J1"]\n'
        "}"
    )
    out = _ejecutar_paso(system, instruccion, hechos, chunks)
    out["_chunks"] = chunks
    return out


# --- Paso 5: Memorando -------------------------------------------------------
def paso5_memo(hechos: dict[str, Any], p1, p2, p3, p4,
               registro: dict[str, dict[str, Any]]) -> dict[str, Any]:
    system = (
        "Eres un abogado senior de RCE en Colombia. Redactas un memorando interno de "
        "ESTRATEGIA DEFENSIVA para el equipo, integrando el análisis previo. Ordenas los "
        "argumentos por SOLIDEZ jurídica y eres honesto sobre debilidades."
    )
    insumo = {
        "regimen": {k: v for k, v in p1.items() if k != "_chunks"},
        "exoneracion": {k: v for k, v in p2.items() if k != "_chunks"},
        "perjuicio": {k: v for k, v in p3.items() if k != "_chunks"},
        "terceros": {k: v for k, v in p4.items() if k != "_chunks"},
    }
    etiquetas_validas = ", ".join(sorted(registro.keys())) or "(ninguna)"
    user = (
        f"HECHOS:\n{json.dumps(hechos, ensure_ascii=False)}\n\n"
        f"ANÁLISIS DE LOS PASOS 1-4:\n{json.dumps(insumo, ensure_ascii=False)}\n\n"
        f"Etiquetas de cita válidas (usa solo estas): {etiquetas_validas}\n\n"
        "Redacta el memorando y devuelve este JSON:\n"
        "{\n"
        '  "sintesis_estrategia":"3-5 frases con la tesis defensiva central",\n'
        '  "argumentos":[\n'
        '    {"tesis":"enunciado del argumento",\n'
        '     "solidez":"SOLIDO | PROBABLE | DEBIL",\n'
        '     "desarrollo":"fundamentación jurídica y fáctica",\n'
        '     "requiere_revision_abogado":"qué debe decidir o verificar el abogado",\n'
        '     "citas":["J1","L2"]}\n'
        "  ],\n"
        '  "siguientes_pasos":["acciones procesales recomendadas"],\n'
        '  "advertencias":["riesgos, hechos controvertidos, vacíos probatorios"]\n'
        "}\n\n"
        "SOLIDO = norma/jurisprudencia clara + hecho probado. PROBABLE = requiere prueba "
        "de descargo. DEBIL = especulativo. " + _REGLA_CITAS
    )
    try:
        raw = _llm(system, user, model=MEMO_MODEL, max_tokens=4096)
        return _parse_json(raw)
    except Exception as e:
        return {"error": str(e), "sintesis_estrategia": None, "argumentos": []}


# --- Render markdown determinista del memo -----------------------------------
_ORDEN = {"SOLIDO": 0, "PROBABLE": 1, "DEBIL": 2}


def _cita_str(labels: list[str], registro: dict[str, dict[str, Any]]) -> str:
    vistos = []
    for lab in labels or []:
        info = registro.get(lab)
        if info:
            vistos.append(f"[{lab}] {info['fuente']} p.{info['pagina']}")
    return "  \n".join(vistos)


def render_memo_markdown(memo: dict[str, Any], registro: dict[str, dict[str, Any]]) -> str:
    L = ["# Memorando de estrategia defensiva", "", f"> {_DISCLAIMER}", ""]
    if memo.get("sintesis_estrategia"):
        L += ["## Síntesis", memo["sintesis_estrategia"], ""]

    args = sorted(memo.get("argumentos", []),
                  key=lambda a: _ORDEN.get(str(a.get("solidez", "DEBIL")).upper(), 3))
    if args:
        L += ["## Argumentos (ordenados por solidez)", ""]
        for i, a in enumerate(args, 1):
            L.append(f"### {i}. [{a.get('solidez','?')}] {a.get('tesis','')}")
            if a.get("desarrollo"):
                L += ["", a["desarrollo"]]
            cs = _cita_str(a.get("citas", []), registro)
            if cs:
                L += ["", "**Fundamento:**  ", cs]
            if a.get("requiere_revision_abogado"):
                L += ["", f"⚠️ **Requiere abogado:** {a['requiere_revision_abogado']}"]
            L.append("")

    if memo.get("siguientes_pasos"):
        L += ["## Siguientes pasos"] + [f"- {p}" for p in memo["siguientes_pasos"]] + [""]
    if memo.get("advertencias"):
        L += ["## Advertencias"] + [f"- {a}" for a in memo["advertencias"]] + [""]

    usadas = {lab for a in args for lab in (a.get("citas") or [])}
    if usadas:
        L += ["## Fuentes citadas"]
        for lab in sorted(usadas):
            info = registro.get(lab)
            if info:
                L.append(f"- **[{lab}]** {info['fuente']}, p.{info['pagina']} ({info['categoria']})")
    return "\n".join(L)


# --- Orquestador -------------------------------------------------------------
def construir_memo(hechos: dict[str, Any]) -> dict[str, Any]:
    """Corre la cadena completa y devuelve el análisis + memo (estructurado y markdown)."""
    if hechos.get("error"):
        return {"error": "los hechos vienen con error; no se puede razonar", "detalle": hechos}

    p1 = paso1_regimen(hechos)
    p2 = paso2_exoneracion(hechos, p1)
    p3 = paso3_perjuicio(hechos)
    p4 = paso4_terceros(hechos, p1)

    # Registro de citas unificado de todos los pasos
    registro: dict[str, dict[str, Any]] = {}
    for paso in (p1, p2, p3, p4):
        registro.update(_registro_citas(paso.get("_chunks", [])))

    memo = paso5_memo(hechos, p1, p2, p3, p4, registro)
    memo_md = render_memo_markdown(memo, registro)

    limpio = lambda p: {k: v for k, v in p.items() if k != "_chunks"}
    return {
        "regimen": limpio(p1),
        "exoneracion": limpio(p2),
        "perjuicio": limpio(p3),
        "terceros": limpio(p4),
        "memo": memo,
        "memo_markdown": memo_md,
        "fuentes": registro,
        "disclaimer": _DISCLAIMER,
    }


# --- CLI de prueba -----------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python reasoning.py <hechos.json>")
        print("  (hechos.json = salida de comprehension.extraer_hechos)")
        raise SystemExit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        hechos = json.load(f)
    resultado = construir_memo(hechos)
    print(resultado["memo_markdown"])
    print("\n\n===== JSON COMPLETO =====")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))