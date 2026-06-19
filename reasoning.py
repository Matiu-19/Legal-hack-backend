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


def _ejecutar(system: str, instruccion: str, payload: dict[str, Any],
              chunks: list[dict[str, Any]] | None = None,
              max_tokens: int = 6000) -> dict[str, Any]:
    """
    Ejecutor flexible: arma el prompt con un payload arbitrario (hechos + análisis
    previos) y, opcionalmente, material RAG con regla de citas. Si no hay chunks,
    es un paso sin recuperación (análisis del expediente / síntesis procesal).
    """
    bloques = [f"{k.upper()}:\n{json.dumps(v, ensure_ascii=False)}" for k, v in payload.items()]
    user = "\n\n".join(bloques)
    if chunks is not None:
        user += "\n\nMATERIAL JURÍDICO RECUPERADO:\n" + _render_contexto(chunks)
        user += "\n\n" + instruccion + "\n\n" + _REGLA_CITAS
    else:
        user += ("\n\n" + instruccion + "\n\nResponde SOLO con JSON válido, sin backticks. "
                 "No inventes datos: si falta soporte usa \"[DATO PENDIENTE DE VERIFICACIÓN]\".")
    raw = ""
    try:
        raw = _llm(system, user, max_tokens=max_tokens)
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
        "DATO CRÍTICO: lo PRIMERO y más importante es decir si la responsabilidad es SUBJETIVA u "
        "OBJETIVA. Subjetiva = se basa en la culpa (culpa probada art. 2341, culpa presunta, o "
        "responsabilidad médica por lex artis). Objetiva = no depende de culpa, solo exonera la "
        "causa extraña (actividad peligrosa art. 2356, producto defectuoso Ley 1480). El SOAT/seguro "
        "es un régimen especial de cobertura objetiva.\n\n"
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
        '  "naturaleza": "subjetiva | objetiva",\n'
        '  "naturaleza_explicacion": "1 frase: por qué es subjetiva (se prueba culpa) u objetiva (solo causa extraña exonera)",\n'
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
    # Respaldo determinista: garantiza la naturaleza (subjetiva/objetiva) aunque el LLM la omita.
    if not out.get("naturaleza"):
        out["naturaleza"] = _NATURALEZA_POR_REGIMEN.get(out.get("regimen", ""), "")
    out["_chunks"] = chunks
    return out


# Naturaleza (subjetiva/objetiva) por régimen — criterio de evaluación #1.
_NATURALEZA_POR_REGIMEN = {
    "subjetiva_culpa_probada": "subjetiva",
    "subjetiva_culpa_presunta": "subjetiva",
    "medica": "subjetiva",
    "actividad_peligrosa": "objetiva",
    "objetiva_actividad_peligrosa": "objetiva",
    "objetiva_producto": "objetiva",
    "seguro_soat": "objetiva",
}


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


# --- NIVEL 1: Análisis probatorio (inventario + matriz) ----------------------
def paso_probatorio(hechos: dict[str, Any]) -> dict[str, Any]:
    """Inventario de pruebas (1.1) y matriz hecho-prueba-elemento jurídico (1.6).
    No usa RAG: analiza la evidencia del propio expediente."""
    system = (
        "Eres un abogado de la DEFENSA en RCE en Colombia, experto en valoración probatoria "
        "(CGP arts. 164-176). Inventarías la prueba del expediente y la cruzas con los elementos "
        "jurídicos. NO inventas pruebas: solo trabajas con las que aparecen en los hechos.\n\n"
        + _PRINCIPIOS
    )
    instruccion = (
        "Con base en las pruebas y hechos del expediente, devuelve este JSON:\n"
        "{\n"
        '  "inventario": [\n'
        '    {"id":"P-001","tipo":"documental|pericial|testimonial|audiovisual|historia_clinica|factura|poliza|otro",\n'
        '     "descripcion":"qué es","fuente":"archivo","ubicacion":"pág/folio/min si se sabe, o null",\n'
        '     "aporta":"demandante|demandado|autoridad|aseguradora|desconocido",\n'
        '     "elemento_juridico":"hecho|dano|nexo|perjuicio|exoneracion|tercero",\n'
        '     "posicion":"soporta|contradice|neutro|ambiguo",\n'
        '     "calidad_lectura":"legible|parcial|ilegible","confianza":"alto|medio|bajo"}\n'
        "  ],\n"
        '  "matriz_hecho_prueba": [\n'
        '    {"elemento":"hecho_imputable|dano|nexo|cuantificacion|exoneracion","aspecto":"hecho concreto",\n'
        '     "prueba_soporta":"P-00X o -","prueba_contradice":"P-00Y o -",\n'
        '     "estado":"probado_preliminar|parcial|controvertido|no_probado|no_verificable",\n'
        '     "riesgo_demandado":"alto|medio|bajo"}\n'
        "  ],\n"
        '  "documentos_ilegibles": [\n'
        '    {"archivo":"...","problema":"...","elemento_afectado":"...","recomendacion":"..."}\n'
        "  ]\n"
        "}"
    )
    return _ejecutar(system, instruccion, {"hechos": hechos}, chunks=None)


# --- NIVEL 2.2-2.4: Daño, nexo causal e imputación ---------------------------
def paso_elementos(hechos: dict[str, Any], regimen: dict[str, Any]) -> dict[str, Any]:
    """Análisis autónomo de daño (2.2), nexo causal (2.3) e imputación (2.4)."""
    q = _query_base(hechos) + (
        " daño cierto personal directo antijurídico · nexo causal reglas de experiencia · "
        "imputación factor de atribución culpa riesgo · interrupción del nexo causa extraña"
    )
    chunks = recuperar(q, ["jurisprudencia", "doctrina"], hechos, n_prio=3, n_general=3)
    reg = regimen.get("regimen", "desconocido")
    system = (
        "Eres un abogado de la DEFENSA en RCE en Colombia. Analizas por separado los elementos "
        "de la responsabilidad: daño, nexo causal e imputación. Cada uno con su nivel autónomo "
        "(no se suman). El daño debe ser cierto, personal, directo y antijurídico (Henao).\n\n"
        + _PRINCIPIOS
    )
    instruccion = (
        f"Régimen determinado: {reg}. Devuelve este JSON:\n"
        "{\n"
        '  "dano": {"tipos":["muerte|lesion|material|moral|salud|vida_relacion|..."],'
        '"certeza":"alto|medio|bajo","analisis":"¿es cierto, personal, directo y antijurídico?",'
        '"ataque_defensa":"cómo lo cuestiona la defensa","citas":["J1","D1"]},\n'
        '  "nexo_causal": {"fuerza":"debil|medio|fuerte|muy_fuerte","analisis":"qué une la conducta con el daño",'
        '"puntos_debiles":"vacíos que puede atacar la defensa","interrupcion":"culpa_victima|hecho_tercero|fuerza_mayor|ninguna",'
        '"citas":["J1"]},\n'
        '  "imputacion": {"factor":"subjetivo_culpa|riesgo_actividad_peligrosa|objetivo","fuerza":"alto|medio|bajo",'
        '"analisis":"por qué se imputa (o no) al demandado","defensa":"prueba de diligencia o causa extraña según el régimen",'
        '"citas":["L1","J1"]}\n'
        "}"
    )
    out = _ejecutar(system, instruccion, {"hechos": hechos}, chunks=chunks)
    out["_chunks"] = chunks
    return out


# --- NIVEL 2.10: Pruebas adicionales recomendadas ----------------------------
def paso_pruebas_adicionales(hechos: dict[str, Any], regimen: dict[str, Any]) -> dict[str, Any]:
    """Pruebas que la defensa debería solicitar (2.10). Conocimiento procesal, sin RAG."""
    system = (
        "Eres un abogado de la DEFENSA en RCE en Colombia. Recomiendas pruebas concretas que la "
        "defensa debe pedir, cada una conectada a un hecho controvertido y a un elemento de "
        "responsabilidad. Conoces oficios (RUNT, Fiscalía, IPS/EPS, aseguradora, RUES), dictámenes "
        "(reconstrucción, médico legal, pérdida de capacidad laboral, contable/actuarial), "
        "interrogatorio de parte, testimonios y exhibición documental.\n\n" + _PRINCIPIOS
    )
    instruccion = (
        "Devuelve este JSON:\n"
        "{\n"
        '  "pruebas": [\n'
        '    {"prueba":"nombre concreto","tipo":"documental|oficio|dictamen|interrogatorio|testimonio|exhibicion|trasladada",\n'
        '     "hecho_objetivo":"qué hecho prueba o desvirtúa","elemento":"dano|nexo|imputacion|exoneracion|perjuicio|tercero",\n'
        '     "razon":"por qué conviene a la defensa","prioridad":"alta|media|baja"}\n'
        "  ]\n"
        "}"
    )
    return _ejecutar(system, instruccion,
                     {"hechos": hechos, "regimen": regimen.get("regimen")}, chunks=None)


# --- NIVEL 2.7-2.8: Índice de riesgo de condena y conciliación ---------------
def paso_riesgo(hechos, regimen, elementos, exoneracion, perjuicio) -> dict[str, Any]:
    """Índice preliminar jurídico-probatorio de riesgo de condena (2.7) y
    recomendación de conciliación con rango (2.8). Síntesis, sin RAG."""
    system = (
        "Eres un abogado de la DEFENSA en RCE en Colombia. Ponderas CUALITATIVAMENTE (sin sumar "
        "mecánicamente) el riesgo de condena y recomiendas sobre conciliación. Lenguaje obligatorio: "
        "'índice preliminar', NO 'probabilidad de condena'; no predices la decisión judicial.\n\n"
        + _PRINCIPIOS
    )
    payload = {
        "hechos_resumen": hechos.get("resumen_factico"),
        "regimen": regimen.get("regimen"),
        "elementos": {k: v for k, v in elementos.items() if k != "_chunks"},
        "exoneracion": {k: v for k, v in exoneracion.items() if k != "_chunks"},
        "perjuicio": {k: v for k, v in perjuicio.items() if k != "_chunks"},
    }
    instruccion = (
        "Devuelve este JSON:\n"
        "{\n"
        '  "indice_riesgo_condena":"un porcentaje aproximado con su banda, p.ej. \'40% - medio\' (PRELIMINAR)",\n'
        '  "nivel":"bajo|medio|alto|critico",\n'
        '  "razones":["máximo 5 razones del índice"],\n'
        '  "puntos_fuertes_defensa":["..."],\n'
        '  "puntos_debiles_defensa":["..."],\n'
        '  "conciliacion": {"recomendacion":"conciliar|no_conciliar|conciliar_bajo_rango",\n'
        '     "rango_sugerido":"mín-máx o cualitativo","justificacion":"razones jurídicas, probatorias y económicas"},\n'
        '  "advertencia":"este índice no sustituye el criterio del abogado ni predice la decisión judicial"\n'
        "}"
    )
    return _ejecutar(system, instruccion, payload, chunks=None)


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
    L = ["## 2.1 Régimen de responsabilidad", ""]
    nat = (reg.get("naturaleza") or "").upper()
    if nat:
        L.append(f"**Naturaleza:** {nat}" + (f" — {reg['naturaleza_explicacion']}" if reg.get("naturaleza_explicacion") else ""))
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


def _collect_labels(obj: Any, acc: set) -> None:
    """Recoge recursivamente todas las etiquetas de cita ('citas') de una estructura."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "citas" and isinstance(v, list):
                acc.update(x for x in v if isinstance(x, str))
            else:
                _collect_labels(v, acc)
    elif isinstance(obj, list):
        for x in obj:
            _collect_labels(x, acc)


def _render_probatorio(prob: dict[str, Any]) -> list[str]:
    if not prob or "error" in prob:
        return []
    L = ["## NIVEL 1 — Análisis probatorio y fáctico", ""]
    inv = prob.get("inventario") or []
    if inv:
        L += ["### Inventario de pruebas", ""]
        for p in inv:
            loc = f", {p.get('ubicacion')}" if p.get("ubicacion") else ""
            L.append(f"- **{p.get('id','P-?')}** [{p.get('tipo','')}] {p.get('descripcion','')} "
                     f"(_{p.get('fuente','')}{loc}_) — aporta: {p.get('aporta','?')} · "
                     f"elemento: {p.get('elemento_juridico','?')} · {p.get('posicion','?')} · "
                     f"{p.get('calidad_lectura','?')}/{p.get('confianza','?')}")
        L.append("")
    mat = prob.get("matriz_hecho_prueba") or []
    if mat:
        L += ["### Matriz hecho – prueba – elemento jurídico", ""]
        for m in mat:
            L.append(f"- **{m.get('elemento','')}** — {m.get('aspecto','')}  ")
            L.append(f"  soporta: {m.get('prueba_soporta','-')} · contradice: {m.get('prueba_contradice','-')} "
                     f"→ _{m.get('estado','?')}_ (riesgo {m.get('riesgo_demandado','?')})")
        L.append("")
    ileg = prob.get("documentos_ilegibles") or []
    if ileg:
        L += ["### Documentos ilegibles o defectuosos", ""]
        for d in ileg:
            L.append(f"- **{d.get('archivo','')}**: {d.get('problema','')} — afecta {d.get('elemento_afectado','')}. "
                     f"Recomendación: {d.get('recomendacion','')}")
        L.append("")
    return L


def _render_elementos(el: dict[str, Any], reg: dict[str, dict[str, Any]]) -> list[str]:
    if not el or "error" in el:
        return []
    L = ["## 2.2–2.4 Elementos de la responsabilidad", ""]
    d = el.get("dano") or {}
    if d:
        L.append(f"**Daño** (certeza: {d.get('certeza','?')}) — tipos: {', '.join(d.get('tipos',[]) or [])}")
        if d.get("analisis"):
            L.append(d["analisis"])
        if d.get("ataque_defensa"):
            L.append(f"_Ataque de la defensa:_ {d['ataque_defensa']}")
        cs = _cita_str(d.get("citas", []), reg)
        if cs:
            L += ["**Fundamento:**  ", cs]
        L.append("")
    n = el.get("nexo_causal") or {}
    if n:
        L.append(f"**Nexo causal** (fuerza: {n.get('fuerza','?')}) — interrupción: {n.get('interrupcion','ninguna')}")
        if n.get("analisis"):
            L.append(n["analisis"])
        if n.get("puntos_debiles"):
            L.append(f"_Puntos débiles atacables:_ {n['puntos_debiles']}")
        cs = _cita_str(n.get("citas", []), reg)
        if cs:
            L += ["**Fundamento:**  ", cs]
        L.append("")
    im = el.get("imputacion") or {}
    if im:
        L.append(f"**Imputación / factor de atribución** ({im.get('factor','?')}, fuerza: {im.get('fuerza','?')})")
        if im.get("analisis"):
            L.append(im["analisis"])
        if im.get("defensa"):
            L.append(f"_Defensa:_ {im['defensa']}")
        cs = _cita_str(im.get("citas", []), reg)
        if cs:
            L += ["**Fundamento:**  ", cs]
        L.append("")
    return L


def _render_exoneracion(ex: dict[str, Any], reg: dict[str, dict[str, Any]]) -> list[str]:
    if not ex or "error" in ex:
        return []
    causales = ex.get("causales_exoneracion") or []
    if not causales:
        return []
    L = ["## 2.5 Causales de exoneración", ""]
    for c in causales:
        L.append(f"### {c.get('causal','')} — viabilidad {c.get('viabilidad','?')}")
        if c.get("fundamento_factico"):
            L.append(c["fundamento_factico"])
        if c.get("que_probar"):
            L.append(f"_Qué debe probar la defensa:_ {c['que_probar']}")
        cs = _cita_str(c.get("citas", []), reg)
        if cs:
            L += ["**Fundamento:**  ", cs]
        L.append("")
    return L


def _render_perjuicio(pj: dict[str, Any], reg: dict[str, dict[str, Any]]) -> list[str]:
    if not pj or "error" in pj:
        return []
    L = ["## 2.6 Cuestionamiento del perjuicio", ""]
    for r in pj.get("rubros") or []:
        extra = f" (reclamado: {r.get('monto_reclamado')})" if r.get("monto_reclamado") else ""
        L.append(f"### {r.get('rubro','')} — soportado: {r.get('soportado','?')}{extra}")
        if r.get("estandar_probatorio"):
            L.append(f"_Estándar probatorio:_ {r['estandar_probatorio']}")
        if r.get("deficiencia"):
            L.append(f"_Deficiencia:_ {r['deficiencia']}")
        if r.get("ataque"):
            L.append(f"**Ataque:** {r['ataque']}")
        if r.get("herramienta_procesal"):
            L.append(f"_Herramienta procesal:_ {r['herramienta_procesal']}")
        pd = r.get("pruebas_de_descargo") or []
        if pd:
            L.append("_Pruebas de descargo:_ " + "; ".join(pd))
        cs = _cita_str(r.get("citas", []), reg)
        if cs:
            L += ["**Fundamento:**  ", cs]
        L.append("")
    if pj.get("objecion_juramento_estimatorio"):
        L += [f"**Objeción al juramento estimatorio (art. 206 CGP):** {pj['objecion_juramento_estimatorio']}", ""]
    return L


def _render_terceros(t: dict[str, Any], reg: dict[str, dict[str, Any]]) -> list[str]:
    if not t or "error" in t:
        return []
    vs = t.get("vinculaciones") or []
    if not vs:
        return []
    L = ["## 2.7 Vinculación de terceros", ""]
    for v in vs:
        L.append(f"- **{v.get('tipo','')}** → {v.get('destinatario','')} "
                 f"(viabilidad {v.get('viabilidad','?')}): {v.get('justificacion','')}")
        if v.get("requisitos"):
            L.append(f"  _Requisitos:_ {v['requisitos']}")
        cs = _cita_str(v.get("citas", []), reg)
        if cs:
            L.append("  _Fundamento:_ " + cs.replace("  \n", " / "))
    L.append("")
    return L


def _render_riesgo(r: dict[str, Any]) -> list[str]:
    if not r or "error" in r:
        return []
    L = ["## 2.8 Índice preliminar de riesgo y conciliación", ""]
    if r.get("indice_riesgo_condena"):
        L.append(f"**Índice preliminar de riesgo de condena:** {r['indice_riesgo_condena']} "
                 f"({r.get('nivel','')})")
    for tit, key in (("Razones", "razones"),
                     ("Puntos fuertes de la defensa", "puntos_fuertes_defensa"),
                     ("Puntos débiles de la defensa", "puntos_debiles_defensa")):
        items = r.get(key) or []
        if items:
            L.append(f"_{tit}:_")
            L += [f"- {x}" for x in items]
    con = r.get("conciliacion") or {}
    if con:
        L.append(f"**Conciliación:** {con.get('recomendacion','')} — rango: {con.get('rango_sugerido','')}")
        if con.get("justificacion"):
            L.append(con["justificacion"])
    if r.get("advertencia"):
        L.append(f"> {r['advertencia']}")
    L.append("")
    return L


def _render_pruebas_ad(pa: dict[str, Any]) -> list[str]:
    if not pa or "error" in pa:
        return []
    ps = pa.get("pruebas") or []
    if not ps:
        return []
    orden = {"alta": 0, "media": 1, "baja": 2}
    L = ["## 2.9 Pruebas adicionales recomendadas", ""]
    for p in sorted(ps, key=lambda x: orden.get(str(x.get("prioridad", "baja")).lower(), 3)):
        L.append(f"- **[{p.get('prioridad','?')}] {p.get('prueba','')}** ({p.get('tipo','')}) — "
                 f"{p.get('hecho_objetivo','')} [{p.get('elemento','')}]. {p.get('razon','')}")
    L.append("")
    return L


def render_memo_markdown(memo: dict[str, Any], registro: dict[str, dict[str, Any]],
                         secciones: dict[str, Any] | None = None) -> str:
    secciones = secciones or {}
    reg = secciones.get("regimen") or {}
    L = ["# Memorando jurídico preliminar de responsabilidad y estrategia defensiva",
         "", f"> {_DISCLAIMER}", ""]
    if memo.get("sintesis_estrategia"):
        L += ["## Síntesis estratégica", memo["sintesis_estrategia"], ""]

    # NIVEL 1 — probatorio
    L += _render_probatorio(secciones.get("probatorio") or {})

    # NIVEL 2 — jurídico
    L += ["## NIVEL 2 — Análisis jurídico de responsabilidad", ""]
    L += _render_regimen(reg, registro)
    L += _render_elementos(secciones.get("elementos") or {}, registro)
    L += _render_exoneracion(secciones.get("exoneracion") or {}, registro)
    L += _render_perjuicio(secciones.get("perjuicio") or {}, registro)
    L += _render_terceros(secciones.get("terceros") or {}, registro)
    L += _render_riesgo(secciones.get("riesgo") or {})
    L += _render_pruebas_ad(secciones.get("pruebas_adicionales") or {})

    # Estrategia — argumentos por solidez
    args = sorted(memo.get("argumentos", []),
                  key=lambda a: _ORDEN.get(str(a.get("solidez", "DEBIL")).upper(), 3))
    if args:
        L += ["## Estrategia defensiva — argumentos (ordenados por solidez)", ""]
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
        L += ["## Advertencias de revisión humana"] + [f"- {a}" for a in memo["advertencias"]] + [""]

    # Fuentes citadas — recolectadas de TODAS las secciones
    usadas: set = set()
    _collect_labels(secciones, usadas)
    _collect_labels(memo, usadas)
    usadas = {u for u in usadas if u in registro}
    if usadas:
        L += ["## Fuentes citadas (trazabilidad)"]
        for lab in sorted(usadas):
            info = registro[lab]
            L.append(f"- **[{lab}]** {info['fuente']}, p.{info['pagina']} ({info['categoria']})")
    return "\n".join(L)


# --- Orquestador -------------------------------------------------------------
def construir_memo(hechos: dict[str, Any]) -> dict[str, Any]:
    """Corre la cadena completa (NIVEL 1 + NIVEL 2) y devuelve análisis + memo."""
    if hechos.get("error"):
        return {"error": "los hechos vienen con error; no se puede razonar", "detalle": hechos}

    p1 = paso1_regimen(hechos)
    elementos = paso_elementos(hechos, p1)
    p2 = paso2_exoneracion(hechos, p1)
    p3 = paso3_perjuicio(hechos)
    p4 = paso4_terceros(hechos, p1)
    probatorio = paso_probatorio(hechos)
    pruebas_ad = paso_pruebas_adicionales(hechos, p1)
    riesgo = paso_riesgo(hechos, p1, elementos, p2, p3)

    registro: dict[str, dict[str, Any]] = {}
    for paso in (p1, elementos, p2, p3, p4):
        registro.update(_registro_citas(paso.get("_chunks", [])))

    memo = paso5_memo(hechos, p1, p2, p3, p4, registro)
    limpio = lambda p: {k: v for k, v in p.items() if k != "_chunks"}
    secciones = {
        "regimen": limpio(p1),
        "elementos": limpio(elementos),
        "exoneracion": limpio(p2),
        "perjuicio": limpio(p3),
        "terceros": limpio(p4),
        "probatorio": probatorio,
        "pruebas_adicionales": pruebas_ad,
        "riesgo": riesgo,
    }
    memo_md = render_memo_markdown(memo, registro, secciones)
    return {
        **secciones,
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