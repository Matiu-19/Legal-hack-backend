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
def _llm(system: str, user: str, model: str = REASONING_MODEL, max_tokens: int = 6000) -> str:
    from google.genai import types
    client = rag._get_gemini()
    cfg: dict[str, Any] = dict(
        system_instruction=system,
        max_output_tokens=max_tokens,
        temperature=0.2,
    )
    # Desactivar "thinking" de gemini-2.5: si no, consume el presupuesto de
    # salida y trunca el JSON. Para extracción estructurada no lo necesitamos.
    try:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass
    resp = client.models.generate_content(
        model=model, contents=[user], config=types.GenerateContentConfig(**cfg)
    )
    return resp.text or ""


# Principios irrenunciables (de la spec jurídica del equipo de abogados).
_PRINCIPIOS = (
    "PRINCIPIOS IRRENUNCIABLES:\n"
    "1) Sin fuente no hay afirmación: toda conclusión jurídica se apoya en una fuente "
    "verificable del material (norma/jurisprudencia/doctrina) y en hechos del expediente.\n"
    "2) Nunca inventes: ni normas, ni números de sentencia, ni datos, ni cuantías. Si un "
    "dato fáctico no está en los hechos, escríbelo como [DATO PENDIENTE DE VERIFICACIÓN]; "
    "si no hay respaldo jurídico en el material, dilo y déjalo para revisión del abogado.\n"
    "3) Todo es PRELIMINAR y requiere validación humana. NO predices el resultado del proceso "
    "ni afirmas que una prueba 'es válida' o que el demandado 'perderá'.\n"
    "4) El abogado decide; tú organizas, analizas y recomiendas."
)

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
    raw = ""
    try:
        raw = _llm(system, user)
        return _parse_json(raw)
    except Exception as e:
        return {"error": str(e), "raw": raw[:600]}


# --- Paso 1: Régimen ---------------------------------------------------------
def paso1_regimen(hechos: dict[str, Any]) -> dict[str, Any]:
    q = _query_base(hechos) + (
        " régimen de responsabilidad civil · culpa probada art 2341 · culpa presunta · "
        "actividad peligrosa art 2356 · responsabilidad médica lex artis · producto defectuoso "
        "Ley 1480 · seguro SOAT · carga de la prueba art 167 · presunción y causa extraña"
    )
    chunks = recuperar(q, ["jurisprudencia", "doctrina", "ley"], hechos, n_prio=4)
    system = (
        "Eres un abogado litigante experto en responsabilidad civil extracontractual en "
        "Colombia, del lado de la DEFENSA (demandado). Determinas el RÉGIMEN de responsabilidad, "
        "que es la decisión que GOBIERNA todo el análisis (define qué debe probar cada parte).\n\n"
        + _PRINCIPIOS + "\n\n"
        "REGLA DE CONFLICTO: si el caso admite dos regímenes, NO elijas dogmáticamente. Presenta "
        "el principal y el alternativo (el que podría alegar la contraparte o el juez) y marca "
        "`clasificacion_contestable`. El actor suele enmarcar como actividad peligrosa (2356) para "
        "invertir la carga; la defensa puede argumentar culpa probada (2341) para dejar la carga "
        "en el actor.\n\n"
        "OJO con el SEGURO/SOAT: NO es el régimen de responsabilidad del demandado, es una CAPA DE "
        "COBERTURA aparte. En un accidente de tránsito el régimen es 'actividad_peligrosa' (2356) "
        "AUNQUE exista SOAT o una aseguradora demandada; la aseguradora se maneja por llamamiento "
        "en garantía, no cambia el régimen. Usa 'seguro_soat' SOLO si la disputa es exclusivamente "
        "sobre cobertura, amparos o exclusiones de la póliza."
    )
    instruccion = (
        "Determina el RÉGIMEN y devuelve este JSON (campos de la sección 2.1 del estándar):\n"
        "{\n"
        '  "regimen": "subjetiva_culpa_probada | subjetiva_culpa_presunta | actividad_peligrosa | '
        'objetiva_producto | medica | seguro_soat | otro",\n'
        '  "etiqueta_legible": "nombre del régimen en lenguaje natural",\n'
        '  "nivel_confianza": "alto | medio | bajo",\n'
        '  "fundamento_factico": "hechos del expediente que justifican este régimen",\n'
        '  "fundamento_juridico": [{"norma":"ej. C.C. art. 2356","citas":["L1","J2"]}],\n'
        '  "carga_de_la_prueba": "a quién corresponde y qué implica para la defensa",\n'
        '  "diligencia_exonera": true,\n'
        '  "regimen_alternativo": "otro régimen que podría alegar la contraparte o el juez (o null)",\n'
        '  "por_que_no_otro_regimen": "descarte razonado de los regímenes que no aplican",\n'
        '  "clasificacion_contestable": true,\n'
        '  "estrategia_reclasificacion": "si la defensa puede reencuadrar el régimen para mover la '
        'carga (p.ej. 2341 frente a 2356), explícalo; si no, null",\n'
        '  "consecuencia_probatoria": "qué DEBE probar el demandante y qué debe controvertir el '
        'demandado bajo este régimen",\n'
        '  "citas": ["L1","J2","D1"]\n'
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
    q = _query_base(hechos) + (
        " cuantía y tasación del perjuicio · daño emergente prueba documental · "
        "lucro cesante prueba de ingresos tablas de mortalidad valor presente · "
        "daño moral topes jurisprudenciales CSJ en SMLMV · daño a la vida de relación · "
        "daño a la salud · juramento estimatorio art 206 CGP · carga de la prueba art 167 CGP · "
        "reducción del monto indemnizatorio"
    )
    # Cargamos más de 'perjuicios' (corpus especializado) y jurisprudencia.
    chunks = recuperar(q, ["perjuicios", "jurisprudencia"], hechos, n_prio=5, n_general=4)
    system = (
        "Eres un abogado de la DEFENSA experto en TASACIÓN y REDUCCIÓN de perjuicios en "
        "responsabilidad civil en Colombia. Tu trabajo es impugnar, rubro por rubro, la "
        "estimación del daño con argumentos CONCRETOS, cuantificados cuando se pueda y con "
        "fundamento normativo/jurisprudencial. Dominas y aplicas:\n"
        "- Carga de la prueba del quantum (art. 167 CGP): probar el monto es del demandante.\n"
        "- Juramento estimatorio (art. 206 CGP): si la estimación es objetada y no se prueba, "
        "o si hay sobreestimación, procede su objeción y eventual sanción.\n"
        "- Daño emergente: exige prueba DOCUMENTAL (facturas, recibos); sin ella, no se acredita.\n"
        "- Lucro cesante: exige prueba de ingresos reales + cálculo con tablas de mortalidad / "
        "vida probable + traída a VALOR PRESENTE; ataca proyecciones especulativas.\n"
        "- Daño moral: la CSJ fija TOPES en SMLMV; ataca lo que exceda el tope y la falta de "
        "prueba de la afectación.\n"
        "- Daño a la vida de relación y daño a la salud: exigen prueba ESPECÍFICA y concreta de "
        "la afectación, no presunciones."
    )
    instruccion = (
        "Impugna la estimación del perjuicio. Si la demanda NO cuantifica o no aporta soporte, "
        "conviértelo en un ataque concreto: exigir cuantificación y prueba conforme a los arts. "
        "167 y 206 CGP. Devuelve este JSON:\n"
        "{\n"
        '  "rubros": [\n'
        '    {"rubro":"dano_emergente | lucro_cesante | dano_moral | dano_vida_relacion | dano_salud | otro",\n'
        '     "monto_reclamado":"texto o null",\n'
        '     "soportado":"si | parcial | no | no_cuantificado",\n'
        '     "estandar_probatorio":"qué exige la ley/jurisprudencia para acreditar ESTE rubro",\n'
        '     "deficiencia":"qué le falta concretamente a la estimación del demandante",\n'
        '     "ataque":"argumento concreto de la defensa (cuantificado si es posible)",\n'
        '     "herramienta_procesal":"p.ej. objetar el juramento estimatorio (206 CGP), exigir prueba (167), pedir perito contable",\n'
        '     "pruebas_de_descargo":["pruebas que la defensa debe solicitar"],\n'
        '     "citas":["P1","J2"]}\n'
        "  ],\n"
        '  "objecion_juramento_estimatorio":"si hay estimación juramentada sobreestimada o sin prueba, fundamenta la objeción (206 CGP) y la posible sanción; si no aplica, null",\n'
        '  "recordatorio_carga_prueba":"nota sobre art. 167 CGP: el quantum lo prueba el demandante",\n'
        '  "citas":["P1","J2"]\n'
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
        "ESTRATEGIA DEFENSIVA para el equipo, integrando el análisis previo. El memorando "
        "ABRE con el régimen de responsabilidad (define cómo se lee todo lo demás), y luego "
        "ordena los argumentos por SOLIDEZ jurídica. Eres honesto sobre debilidades.\n\n"
        + _PRINCIPIOS
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
        '  "sintesis_estrategia":"3-5 frases con la tesis defensiva central; DEBE empezar nombrando el régimen de responsabilidad aplicable",\n'
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


def _render_regimen(reg: dict[str, Any], registro: dict[str, dict[str, Any]]) -> list[str]:
    """Sección prominente del régimen (criterio #1 de evaluación)."""
    if not reg or "error" in reg:
        return []
    nombre = reg.get("etiqueta_legible") or reg.get("regimen") or "no determinado"
    L = ["## 1. Régimen de responsabilidad", ""]
    conf = reg.get("nivel_confianza")
    L.append(f"**Régimen aplicable:** {nombre}" + (f"  ·  confianza: {conf}" if conf else ""))
    if reg.get("carga_de_la_prueba"):
        L.append(f"**Carga de la prueba:** {reg['carga_de_la_prueba']}")
    if reg.get("diligencia_exonera") is not None:
        L.append(f"**¿La diligencia exonera?:** {'Sí' if reg['diligencia_exonera'] else 'No (solo causa extraña)'}")
    if reg.get("clasificacion_contestable") and reg.get("regimen_alternativo"):
        L.append(f"**⚠️ Clasificación contestable** — régimen alternativo: {reg['regimen_alternativo']}.")
        if reg.get("estrategia_reclasificacion"):
            L.append(f"**Estrategia de reclasificación:** {reg['estrategia_reclasificacion']}")
    if reg.get("consecuencia_probatoria"):
        L.append(f"**Consecuencia probatoria:** {reg['consecuencia_probatoria']}")
    # Fundamento jurídico con citas expandidas (deduplicadas, en orden)
    fj_labels: list[str] = list(reg.get("citas", []))
    for fj in reg.get("fundamento_juridico", []):
        fj_labels += fj.get("citas", [])
    fj_labels = list(dict.fromkeys(fj_labels))
    cs = _cita_str(fj_labels, registro)
    if cs:
        L += ["", "**Fundamento jurídico:**  ", cs]
    L.append("")
    return L


def render_memo_markdown(memo: dict[str, Any], registro: dict[str, dict[str, Any]],
                         regimen: dict[str, Any] | None = None) -> str:
    L = ["# Memorando de estrategia defensiva", "", f"> {_DISCLAIMER}", ""]
    if memo.get("sintesis_estrategia"):
        L += ["## Síntesis", memo["sintesis_estrategia"], ""]

    # Régimen primero — es el criterio que más pesa y gobierna todo el análisis.
    L += _render_regimen(regimen or {}, registro)

    args = sorted(memo.get("argumentos", []),
                  key=lambda a: _ORDEN.get(str(a.get("solidez", "DEBIL")).upper(), 3))
    if args:
        L += ["## 2. Argumentos defensivos (ordenados por solidez)", ""]
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
    if regimen:
        usadas |= set(regimen.get("citas", []))
        for fj in regimen.get("fundamento_juridico", []):
            usadas |= set(fj.get("citas", []))
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
    limpio = lambda p: {k: v for k, v in p.items() if k != "_chunks"}
    memo_md = render_memo_markdown(memo, registro, regimen=limpio(p1))
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