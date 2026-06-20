# Asistente de defensa en Responsabilidad Civil Extracontractual (RCE)

**Legal Hack Icesi 2026 · Reto 2 — Hurtado Gandini Abogados**

Backend de IA que analiza demandas de **responsabilidad civil extracontractual**
en Colombia desde la perspectiva de la **parte demandada** y produce un
**memorando de estrategia defensiva** trazable, con un PDF premium descargable.

---

## El problema

Las demandas de RCE (accidentes de tránsito, actividades peligrosas,
responsabilidad médica, productos defectuosos) son únicas y complejas: el abogado
debe determinar el régimen de responsabilidad, evaluar causales de exoneración,
cuestionar la cuantía del perjuicio y decidir la vinculación de terceros, leyendo
expedientes multimodales (PDF nativo o escaneado, imágenes, video, audio). Es
lento y propenso a omisiones.

## La solución

Un pipeline que recibe el expediente multimodal y devuelve un **memorando de
análisis y estrategia defensiva** (no un documento procesal) que **razona
jurídicamente y cita fuentes verificables**:

```
Ingesta multimodal → Comprensión (hechos) → [RAG jurídico] → Cadena de razonamiento → Memorando (HTML/PDF)
   PDF/img/video/audio   hechos JSON          ~35k chunks      régimen → exoneración →     diseño Hurtado
   (texto o imagen)      estructurados        (ChromaDB)        perjuicio → terceros        Gandini
```

- **Régimen** (criterio clave): determina si la responsabilidad es **subjetiva u
  objetiva**, marcado de forma prominente y con fundamento citado.
- **Exoneración**: causales de causa extraña condicionadas al régimen.
- **Perjuicio**: ataque rubro por rubro con arsenal procesal (juramento
  estimatorio art. 206 CGP, carga de la prueba art. 167, topes de daño moral).
- **Terceros**: llamamiento en garantía / denuncia del pleito.
- **Trazabilidad**: cada argumento cita norma/jurisprudencia recuperada del RAG
  (guardrail: solo se cita lo que el retriever devolvió; no se inventan sentencias).

## Stack técnico

| Capa | Tecnología |
|---|---|
| API | FastAPI + Hypercorn (HTTP/2) |
| LLM | Gemini 2.5 Flash vía **Vertex AI** (Google Cloud) |
| RAG | ChromaDB + embeddings `gemini-embedding-001` (768 dims) |
| OCR | Tesseract (PDFs escaneados, solo en indexación) |
| Multimodal | PyMuPDF (PDF→texto/imagen), ffmpeg + faster-whisper (video/audio) |
| PDF premium | HTML + Chromium headless (Playwright) |
| Infra | Docker · Google Cloud Run · GCS (índice RAG) · GitHub Actions (CD) |

---

## Cómo ejecutarlo en local

**Requisitos:** Python 3.11, ffmpeg, Tesseract (solo para indexar escaneados) y
credenciales de Google Cloud (Vertex AI).

```bash
# 1. Entorno
python -m venv venv
venv\Scripts\activate                 # Windows  (o: source venv/bin/activate)
pip install -r requirements.txt
python -m playwright install chromium # navegador para los PDFs

# 2. Credenciales (Vertex AI)
gcloud auth application-default login

# 3. Variables de entorno -> archivo .env
#    GOOGLE_CLOUD_PROJECT=tu-proyecto-gcp
#    GCS_BUCKET=tu-bucket             (donde vive el índice RAG)
#    GEMINI_API_KEY=...               (opcional, fallback de AI Studio)

# 4. Correr la API
hypercorn main:app --bind 0.0.0.0:8000
#    Docs interactivas: http://127.0.0.1:8000/docs
```

### Construir el índice RAG (una sola vez)

Coloca el corpus jurídico en subcarpetas (`JURISPRUDENCIA`, `LEYES`, `DOCTRINA`,
`CONCEPTOS - ACTOS ADMINISTRATIVOS`, `TODO SOBRE DAÑO...`) y ejecuta:

```bash
python index_corpus.py "ruta/al/corpus" --reset --subir   # indexa y sube a GCS
python rag.py listar                                       # chunks por categoría
```

---

## Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/analizar` | Expediente por categorías (`demanda`, `pruebas`, `anexos`, `poderes`) → `{hechos, analisis, memo}` |
| `POST` | `/pdf/memo` | `{analisis, hechos}` → **memorando.pdf** (diseño premium) |
| `POST` | `/pdf/hechos` | `hechos` → **ficha_caso.pdf** |
| `POST` | `/memo/html` | Igual que `/pdf/memo` pero devuelve HTML (preview) |
| `GET`  | `/rag-status` | Estado del índice RAG |
| `GET`  | `/health` | Healthcheck |

---

## Estructura del proyecto

```
ingest.py          Ingesta multimodal → bloques (PDF texto/imagen, video, audio)
transcribe.py      Transcripción de audio (faster-whisper, opcional)
comprehension.py   Bloques → hechos jurídicos estructurados (JSON)
rag.py             RAG legal: OCR, embeddings, ChromaDB, sync con GCS
index_corpus.py    Job batch para indexar el corpus jurídico (offline)
reasoning.py       Cadena de razonamiento (régimen → exoneración → perjuicio → terceros → memo)
memo_html.py       Render del memorando/ficha a HTML premium (plantilla Hurtado Gandini)
pdf_render.py      HTML → PDF con Chromium headless
main.py            API FastAPI (endpoints)
Dockerfile         Imagen para Cloud Run (ffmpeg + Chromium)
```

---

## Despliegue (Google Cloud Run)

CD automático: cada `push` a `main` dispara `.github/workflows/deploy.yml`.
Despliegue manual:

```bash
gcloud run deploy apphack-rce --source . --region us-central1 \
  --allow-unauthenticated --memory 4Gi --cpu 2 --min-instances 1 \
  --use-http2 --timeout 600 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=tu-proyecto,GCS_BUCKET=tu-bucket
```

> El servicio descarga el índice RAG desde GCS al arrancar (Cloud Run no tiene
> disco persistente). HTTP/2 elimina el límite de 32 MB de subida del proxy.

---

## Nota sobre el uso de IA

El sistema es un **asistente**, no un sustituto del abogado. Todo el output es
**preliminar** y exige validación humana. No predice resultados judiciales ni
inventa fuentes: cada cita se recupera del corpus verificado y se marca para
revisión. La estrategia final siempre la decide el abogado.