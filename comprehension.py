"""
Comprensión — bloques multimodales -> hechos jurídicos estructurados (JSON).

Toma la lista de bloques que produce `ingest.normalize_many(...)` y hace UNA
llamada multimodal a Gemini que devuelve los hechos en un esquema fijo. No
califica el régimen ni propone estrategia: eso lo hace la cadena de
razonamiento jurídica que se conecta después.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

from google import genai
from google.genai import types
from dotenv import load_dotenv
load_dotenv()

MODEL = os.environ.get("COMPREHENSION_MODEL", "gemini-2.5-flash")
MAX_TOKENS = 16384   # demandas reales extensas: muchos hechos/daños/pruebas

SYSTEM = """Eres un asistente jurídico experto en responsabilidad civil
extracontractual en Colombia. Recibes la evidencia de una demanda (texto,
documentos PDF, imágenes y/o transcripciones de video) y extraes ÚNICAMENTE
los hechos relevantes. No opines sobre el régimen de responsabilidad ni sobre
la estrategia: solo describe lo que la evidencia dice.

Devuelve EXCLUSIVAMENTE un objeto JSON válido, sin texto adicional y sin
backticks, con esta estructura exacta:

{
  "resumen_factico": "3-6 frases neutrales de qué ocurrió",
  "tipo_caso": {
    "categoria": "transito | actividad_peligrosa | medica | producto | otro",
    "fundamento": "cita textual o descripción del fragmento de la demanda que establece el tipo de caso"
  },
  "partes": {
    "demandantes": ["string"],
    "demandados_potenciales": ["string"]
  },
  "hechos": [
    {"hecho": "string", "fecha": "string o null", "fuente": "nombre del archivo"}
  ],
  "danos_alegados": [
    {"tipo": "string", "descripcion": "string", "fuente": "string"}
  ],
  "pruebas_aportadas": [
    {"tipo": "documento | peritaje | testimonio | audiovisual | otro",
     "descripcion": "string", "fuente": "string"}
  ],
  "peritajes": [
    {"materia": "string", "conclusion": "string", "fuente": "string"}
  ],
  "cuantia": {
    "monto_total": "string o null",
    "rubros": [
      {"rubro": "dano_emergente | lucro_cesante | dano_moral | otro",
       "monto": "string o null", "soporte": "string o null"}
    ]
  },
  "vacios_o_dudas": ["datos faltantes, ilegibles o contradictorios"]
}

Reglas:
- Cada hecho, daño y prueba DEBE indicar su `fuente` (el archivo de donde sale).
- No inventes datos. Si algo no aparece, usa null o lista vacía y anótalo en
  `vacios_o_dudas`.
- `tipo_caso.categoria` debe extraerse de lo que la demanda dice explícitamente
  (ej: "accidente de tránsito", "acto médico", "producto defectuoso", actividad
  que involucra riesgo). Si la demanda no lo dice con claridad, usa "otro" y
  explícalo en `fundamento`.
- `tipo_caso.fundamento` debe citar el texto o sección del documento que
  permite determinar la categoría. Es el respaldo trazable de la clasificación.
"""

INSTRUCCION = (
    "Extrae los hechos jurídicos relevantes de toda la evidencia anterior y "
    "devuelve únicamente el JSON con la estructura indicada."
)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
        if project:
            # Vertex AI — usa credenciales GCP (automático en Cloud Run)
            _client = genai.Client(vertexai=True, project=project, location="us-central1")
        elif api_key:
            # AI Studio — desarrollo local
            _client = genai.Client(api_key=api_key)
        else:
            raise RuntimeError(
                "Define GOOGLE_CLOUD_PROJECT (Vertex AI) o GEMINI_API_KEY (AI Studio)."
            )
    return _client


def _blocks_to_parts(blocks: list[dict[str, Any]]) -> list[Any]:
    """Convierte los bloques internos al formato de partes que acepta el nuevo SDK."""
    parts: list[Any] = []
    for block in blocks:
        if block["type"] == "text":
            parts.append(block["text"])
        elif block["type"] in ("image", "document"):
            src = block["source"]
            data = base64.standard_b64decode(src["data"])
            parts.append(types.Part.from_bytes(data=data, mime_type=src["media_type"]))
    return parts


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text, strict=False)   # tolera saltos de línea en strings


def extraer_hechos(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Llama a Gemini con los bloques multimodales y devuelve los hechos."""
    client = _get_client()
    parts = _blocks_to_parts(blocks) + [INSTRUCCION]
    cfg: dict[str, Any] = dict(system_instruction=SYSTEM, max_output_tokens=MAX_TOKENS)
    # Desactivar "thinking" de gemini-2.5: si no, consume el presupuesto de salida
    # y trunca el JSON en demandas extensas (con muchos hechos/daños/pruebas).
    try:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass
    response = client.models.generate_content(
        model=MODEL, contents=parts, config=types.GenerateContentConfig(**cfg)
    )
    raw = response.text or ""
    try:
        return _parse_json(raw)
    except Exception as e:
        return {"error": "no se pudo parsear el JSON", "detalle": str(e), "raw": raw}