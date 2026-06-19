# Contexto de sesión — Reto 2 RCE (LegalHack Icesi 2026)

Índice de recuperación de contexto. Versión visual: artifact publicado en claude.ai.

## Qué construimos
Asistente de IA para el abogado **demandado** en Responsabilidad Civil Extracontractual (Colombia).
Entregable = **memorando de estrategia defensiva** (no documento procesal). Clave: razonamiento jurídico **trazable** con citas verificables.

Pipeline: `Ingesta multimodal → Comprensión (hechos) → [RAG jurídico] → Razonamiento → Memo`

## Infraestructura y credenciales (CRÍTICO)
| Item | Valor |
|---|---|
| Cloud Run URL | https://apphack-rce-723971697441.us-central1.run.app |
| GCP Project ID | `gen-lang-client-0398885476` |
| GCP Project Number | `723971697441` |
| Región | `us-central1` |
| Cloud Run service | `apphack-rce` |
| GCS bucket (índice) | `apphack-rce-docs` |
| Objeto índice | `chromadb-index.tar.gz` |
| Runtime SA | `723971697441-compute@developer.gserviceaccount.com` |
| Deploy SA (GitHub) | `github-deployer@gen-lang-client-0398885476.iam.gserviceaccount.com` |
| GitHub Secret | `GCP_SA_KEY` (contenido de key.json) |

**IA/LLM:** Producción usa **Vertex AI** (créditos GCP, sin API key). La key de **AI Studio está AGOTADA**. Local también usa Vertex vía `gcloud auth application-default login`.
**Variable clave en Cloud Run:** `GOOGLE_CLOUD_PROJECT=gen-lang-client-0398885476`. Si falta, cae a la key agotada → error. **NUNCA poner GEMINI_API_KEY en Cloud Run.**

## Archivos
| Archivo | Qué hace | Estado |
|---|---|---|
| `ingest.py` | PDF/imagen/video/audio → bloques. PDF→imágenes por página (PyMuPDF). Recarga PATH ffmpeg. | listo |
| `comprehension.py` | Bloques → hechos JSON (gemini-2.5-flash/Vertex). `tipo_caso` con `fundamento` trazable. | listo |
| `transcribe.py` | Transcripción audio opcional (faster-whisper). | listo |
| `rag.py` | RAG: OCR Tesseract, embeddings 768d, 5 categorías, reintentos, resume, sync GCS. | listo |
| `index_corpus.py` | Job batch: recorre carpetas, clasifica, indexa 80 PDFs. | listo |
| `main.py` | FastAPI `POST /analizar` (HTTP/2, sin límite). Descarga índice GCS al arrancar. HOOK conectado → devuelve `analisis` + `memo`. | listo |
| `reasoning.py` | Cadena de 5 pasos con RAG por categoría + citas trazables + memo por solidez. Compila; FALTA probar end-to-end. | listo (sin probar) |
| `Dockerfile` | Python 3.11 + ffmpeg + hypercorn (HTTP/2). | listo |
| `.github/workflows/deploy.yml` | CD: push a main → deploy. | listo |

## Decisiones técnicas (no revertir sin discutir)
- SDK `google-genai` (nuevo); el viejo `google-generativeai` da error 400.
- PDF grande → página→imagen con PyMuPDF (inline falla >~20-32MB).
- ChromaDB local (no Pinecone).
- Embeddings `gemini-embedding-001` a **768 dims** normalizados (Matryoshka).
- OCR Tesseract español; `tessdata/spa.traineddata` local (no hubo admin para Program Files).
- Índice se construye local → sube a GCS → Cloud Run lo baja al arrancar.
- Uploads >32MB: HTTP/2 + hypercorn + `--use-http2` (el límite es del proxy de Cloud Run).

## RAG — 5 categorías (raíz: `C:\Users\MSI\Downloads\HACKATHON`)
- `jurisprudencia` (prioridad máxima) — 22 PDFs
- `ley` — 19 · `acto_admin` — 17 · `doctrina` — 14 · `perjuicios` — 8

## Pendiente (TODO)
1. **Probar `reasoning.py` end-to-end** una vez termine la indexación (ver abajo).
2. `POST /ingerir` — subir docs al RAG en vivo tras deploy.
3. Frontend Next.js — hechos + memo + click-to-source.
4. Validar índice: `python rag.py listar`.
5. Redesplegar a Cloud Run con el razonamiento + subir índice a GCS.

### Cómo probar reasoning al despertar
```bash
# 1. Confirmar que la indexación terminó y subió a GCS
python rag.py listar
# 2. Generar hechos de una demanda y guardarlos
python -c "import json,sys; from ingest import normalize; from comprehension import extraer_hechos; json.dump(extraer_hechos(normalize(r'C:\Users\MSI\Downloads\demanda.pdf')), open('hechos.json','w',encoding='utf-8'), ensure_ascii=False)"
# 3. Correr la cadena de razonamiento sobre esos hechos
python reasoning.py hechos.json
```
`reasoning.construir_memo(hechos)` devuelve: `regimen`, `exoneracion`, `perjuicio`, `terceros`, `memo` (estructurado), `memo_markdown`, `fuentes` (mapa de citas → fuente/página).

## Cheat sheet
```bash
# Indexación (local, Vertex). Resumible: re-correr SIN --reset retoma.
gcloud auth application-default login
python index_corpus.py "C:\Users\MSI\Downloads\HACKATHON" --reset --subir
python rag.py listar
python rag.py subir / descargar
python rag.py consultar "nexo causal actividad peligrosa" --n 6 --cat jurisprudencia

# Backend local (HTTP/2)
hypercorn main:app --bind 0.0.0.0:8000
curl.exe -X POST http://127.0.0.1:8000/analizar -F "files=@demanda.pdf"

# Deploy manual
gcloud run deploy apphack-rce --source . --region us-central1 \
  --allow-unauthenticated --memory 2Gi --cpu 1 --use-http2 --timeout 600 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=gen-lang-client-0398885476
gcloud run services logs read apphack-rce --region us-central1 --limit 50

# Deploy automático
git push origin main
```

## Estado al dormir
Indexación de 80 PDFs corriendo en background (`--reset --subir`). PC sin suspensión con corriente (`standby-timeout-ac 0`). Dejar enchufado, tapa abierta, sesión iniciada. Salida bufferizada (normal); al terminar sube el índice a GCS solo.
```
```
Modelos: comprensión `gemini-2.5-flash` · embeddings `gemini-embedding-001` (768d) · vía Vertex AI.