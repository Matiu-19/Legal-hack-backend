# CLAUDE.md — Reto 2 RCE · Asistente de contestación de demandas

Contexto del proyecto para Claude Code. Léelo completo antes de proponer
cambios. Si algo aquí choca con el código, este archivo manda salvo que el
usuario diga lo contrario.

---

## 0. Cambios recientes (LEE ESTO PRIMERO)

- Los abogados entregaron una **spec ampliada** (`PROYECTO_GANADOR.pdf`) con dos
  niveles de análisis y un **Módulo 1 de valoración probatoria** muy detallado.
  Ver §5.
- **Decisión de RAG ACTUALIZADA:** antes se descartó RAG porque el corpus era
  mínimo (~30 normas). Con la nueva spec el acervo es grande (códigos completos,
  decenas de sentencias de la Sala Civil, 10 libros de doctrina) y **ya NO cabe
  en contexto**, así que **se usa RAG para la capa de conocimiento/citas**. Ver §3 y §9.
- **Riesgo #1 = alcance, no técnica.** La spec describe una plataforma de
  producción; para el hackathon hay que triajear duro. Ver §11.

## 1. Qué es el reto

Hackathon legal-tech. Asistente que apoya al abogado en el análisis de
**demandas de responsabilidad civil extracontractual (RCE)** en Colombia, desde
la perspectiva de la **parte demandada**.

- **Entregable:** un **memorando de análisis y estrategia defensiva** (NO un
  documento procesal). El abogado lo usa para redactar la contestación.
- **Diferenciador:** el sistema debe **razonar jurídicamente y ser trazable**,
  no solo extraer texto. Lo que puntúa es el razonamiento y la cita verificable.
- **Entrada:** expedientes multimodales (PDF nativo o escaneado, imágenes,
  video, audio, dictámenes, historias clínicas, facturas, pólizas, etc.).

**Estructura en 2 niveles (de la spec de los abogados):**
- **Nivel 1 — Análisis de los hechos:** Módulo 1, valoración probatoria y
  análisis fáctico. → Es nuestra **capa de comprensión ampliada**.
- **Nivel 2 — Análisis jurídico:** (ii) responsabilidad, (iii) perjuicio,
  (iv) terceros. → Es nuestra **cadena de razonamiento**.

La capa de lectura ya construida (§4) es el cimiento de ambos niveles.

## 2. Criterios de evaluación (optimiza para esto)

1. **Identificación del régimen** — subjetiva (probada/presunta) u objetiva.
2. **Análisis de exoneración** — causales pertinentes con base normativa.
3. **Cuestionamiento del perjuicio** — argumentos concretos contra el daño.
4. **Calidad del memorando** — útil y accionable.
5. **Trazabilidad jurídica** — cada argumento cita norma/jurisprudencia **verificable**.

Criterios 1 y 2 son los que más pesan. El 5 se gana con el RAG + guardrail (§9).

## 3. Arquitectura (HÍBRIDA — RAG + cadena determinista)

```
Ingesta multimodal  ->  Módulo 1: Valoración probatoria  ->  Nivel 2: Cadena de razonamiento  ->  Memo
   [construido]            [extender comprensión]               [con grounding RAG]                [por hacer]
                                                                        |
                                                                 [RAG: base jurídica]
```

Decisiones (no revertir sin discutir):

- **RAG = SÍ, pero solo para la capa de conocimiento/citas.** El árbol de
  régimen, la exoneración y los ataques al perjuicio son **lógica determinista**,
  NO RAG. La cadena razona; el RAG trae la sentencia/norma exacta para citarla.
  **RAG aumenta, no gobierna:** si el retrieval falla, el razonamiento se
  mantiene (el régimen es lógica pura) y solo degrada la cita.
- **Cadena determinista en Python, NO un agente autónomo.** Más fácil de
  demostrar y depurar. Cada etapa devuelve JSON estructurado.
- **Backend Python (FastAPI)** dueño de ingesta + comprensión + RAG + cadena +
  memo. **Front Next.js** dueño de UI, subida de archivos y render.
- La key de Claude vive en el backend (`ANTHROPIC_API_KEY`), nunca en el browser.

## 4. Estado actual del código (capa de lectura — YA CONSTRUIDA)

```
ingest.py         normalize(path)/normalize_many(paths) -> bloques para Claude
                  PDF (digital o escaneado) y imagen: nativos, sin OCR externo.
                  video: frames con ffmpeg (cap MAX_VIDEO_FRAMES) + transcripción.
                  Cada archivo se antecede con marcador de fuente (trazabilidad).
transcribe.py     transcripción opcional (faster-whisper); degrada con gracia.
comprehension.py  extraer_hechos(blocks) -> hechos JSON (1 llamada multimodal).
main.py           FastAPI: POST /analizar -> {ok, archivos, hechos, memo}
                  contiene el HOOK donde se conecta el Nivel 2.
requirements.txt  anthropic, fastapi, uvicorn, python-multipart (+ faster-whisper opcional).
```

Correr: `uvicorn main:app --reload --port 8000`. Probar: `http://127.0.0.1:8000/docs`.

## 5. Módulo 1 — Valoración probatoria (Nivel 1)

**Regla clave de implementación:** NO son 11 motores separados. Es **UN esquema
de extracción estructurada rica** que el modelo llena en (idealmente) una o
pocas llamadas multimodales — extiende la capa de comprensión ya construida.
No construyas 11 features; construye el esquema y deja que Claude lo llene.

Salidas (campos del esquema, no subsistemas):

- **Inventario de pruebas:** por cada prueba → `id` (P-001...), tipo, archivo,
  ubicación (página/folio/minuto), parte que la aporta, hecho relacionado,
  calidad de lectura (legible/ilegible), nivel de confianza (alto/medio/bajo).
- **Extracción fina de datos:** personas, tiempo, lugar, vehículos (placas,
  póliza, SOAT), salud (diagnóstico, incapacidad), dinero (facturas, ingresos),
  peritos, terceros. Distinguir **dato probado vs. dato solo afirmado**.
- **Matriz hecho-prueba:** hecho de la demanda → prueba que lo soporta / que lo
  contradice → estado (probado preliminar / parcial / controvertido / no probado
  / no verificable) → riesgo para el demandado.
- **Ficha de dictámenes periciales** (prueba clave): perito, especialidad,
  objeto, metodología, soportes, conclusiones, **debilidades**, riesgo,
  recomendación defensiva.
- **Detección de ilegibles:** marcar documentos no leíbles con confiabilidad;
  NO adivinar contenido; emitir alerta + recomendación.
- **Semáforo probatorio:** verde / amarillo / rojo / gris por prueba.
- **Índice preliminar de riesgo fáctico-probatorio** (% con bandas). Llamarlo
  así, NUNCA "probabilidad de perder el caso".

**Regla de oro:** ninguna conclusión fáctica sin **fuente verificable +
ubicación exacta + nivel de confianza + advertencia de revisión humana**.

**Reglas de lenguaje (obligatorias):** todo es "preliminar" y "requiere
validación humana". NUNCA decir "esta prueba es válida / será rechazada / el
demandado perderá". Usar formulaciones de riesgo preliminar.

**Fundamento procesal (CGP):** legalidad, pertinencia, conducencia y utilidad de
la prueba; carga de la prueba (art. 167); sana crítica (art. 176); juramento
estimatorio (art. 206); dictamen pericial (arts. 226-235). Ver §8.

## 6. El contrato — esquema de salida

`hechos` (mínimo, ya construido en `comprehension.py`) es la base. El Módulo 1
lo **extiende** con `inventario`, `matriz_hecho_prueba`, `fichas_peritos`,
`semaforo`, `indice_riesgo`. **Fija el esquema completo con el equipo ANTES de
construir el Nivel 2 encima** — si cambia un campo después, rompe el enganche.

## 7. La lógica jurídica del Nivel 2 (el núcleo que da puntos)

Corre **por cada demandado** (un mismo hecho puede caer en varios regímenes
según a quién se mire → dispara la vinculación de terceros).

### 7.1 Clasificación de régimen (gobierna todo)

Cascada ordenada; primer "sí" gana:
1. ¿Servicio de salud / acto médico? → **Médica**
2. ¿Daño por defecto de producto en el mercado? → **Producto (Ley 1480)**
3. ¿Actividad peligrosa? (vehículo, energía, arma, explosivo, maquinaria) →
   **Actividad peligrosa (2356)**; si es tránsito, + Ley 769 + concurrencia
4. ¿Hecho ajeno o animal? → **Culpa presunta (2347 / 2353-2354)**
5. Default → **Culpa probada (2341)**

| Régimen | Norma | Carga | Exoneración viable | ¿Diligencia exonera? |
|---|---|---|---|---|
| Culpa probada | 2341 | demandante prueba culpa | negar culpa · atacar nexo · causa extraña | **Sí** |
| Culpa presunta | 2347 / 2353-2354 | se presume la culpa | diligencia + causa extraña | Sí (casi no en 2354 fiero) |
| Actividad peligrosa | 2356 | presunción; nexo a cargo del actor | **solo causa extraña** | **No** |
| Producto | Ley 1480 arts. 19-26 (causales art. 22) | objetiva | causales tasadas art. 22 | **No** |
| Médica | Ley 23/81 · Ley 1751 · 2341 | culpa probada / carga dinámica | negar culpa (lex artis) + causa extraña | Sí; medios vs. resultado |

**Crítico:** en régimen objetivo, probar diligencia NO exonera. **La
clasificación es contestable:** el actor enmarca como actividad peligrosa para
invertir la carga; la defensa argumenta culpa probada (2341) para dejar la carga
en el actor. Marcar `clasificacion_contestable` cuando aplique.

### 7.2 Exoneración (condicionada al régimen)
- **Objetivo:** SOLO causa extraña = {culpa exclusiva de la víctima · hecho de
  un tercero · fuerza mayor/caso fortuito}. Diligencia inútil.
- **Subjetivo:** las 3 causas extrañas **+ prueba de diligencia**.
- Cada causal con su `fundamento_id` (cita recuperada por RAG).

### 7.3 Perjuicio (atacar rubro por rubro)
- **Daño emergente:** ¿soporte documental? → ataque: gastos no acreditados.
- **Lucro cesante:** → ataque: proyección especulativa, sin prueba de ingresos.
- **Daño moral:** topes CSJ en SMLMV → ataque: excede topes, falta prueba de afectación.
- (También: daño a la vida de relación, daño a la salud, pérdida de oportunidad.)

### 7.4 Terceros → **llamamiento en garantía** (aseguradora) · **denuncia del pleito** (fabricante).

### 7.5 Memo + trazabilidad (criterios 4 y 5)
- **Orden por solidez:** SÓLIDO (norma + hecho probado) → PROBABLE (requiere
  prueba de descargo) → DÉBIL (especulativo). Primero los sólidos.
- Cada argumento con `cita_id` recuperada del RAG y **validada** (§9).

## 8. Marco normativo (insumo del corpus RAG)

**Normas obligatorias:** Código Civil (2341, 2347, 2353/2354, 2356); CGP
(Ley 1564/2012) — capa probatoria prioritaria: arts. 164-165, 167 (carga),
168 (rechazo), 176 (sana crítica), 206 (juramento estimatorio), 226-235
(dictamen), 243+ (documental), 96 (contestación), 100/282 (excepciones),
64+ (llamamiento en garantía); Ley 2213/2022; Ley 1480/2011 (arts. 19-26);
Decreto 1074/2015; Código de Comercio (seguros); Ley 769/2002 + reformas
(1383/2010, 2252/2022, 2283/2023); Ley 1328/2009; Decreto 2555/2010;
Ley 1581/2012 (datos); Ley 1266/2008.

**Jurisprudencia — tabla de prioridad (ESTE es el alcance de ingesta inicial,
no la biblioteca completa):**

| # | Providencia | Tema |
|---|---|---|
| 1 | SC072-2025 | R. médica, daño a la salud/moral/vida de relación, pérdida de oportunidad, lucro cesante, seguro, llamamiento |
| 2 | SC3280-2024 | Actividad peligrosa, nexo causal, causa extraña, carga del demandado |
| 3 | SC2376-2024 | Valoración racional, sana crítica, apreciación conjunta, indicios |
| 4 | SC3452-2024 | Dictamen pericial, contradicción, sana crítica |
| 5 | SC456-2024 | Reparación integral, pérdida de oportunidad, daño |
| 6 | SC706-2024 | Carga de la prueba en R. civil |
| 7 | SC371-2023 | Seguro de R. civil profesional, exclusiones, llamamiento |
| 8 | SC395-2023 | Relaciones de consumo, garantía legal, consumidor inmobiliario |
| 9 | SC2850-2022 | Garantía legal Ley 1480, término para la acción |
| 10 | SC2111-2021 | Actividades peligrosas concurrentes, hecho exclusivo de la víctima |

+ por problema jurídico: SC069-2023 (hecho de tercero), SC065-2023 (fuerza
mayor), SC3075-2024 (SOAT), SC3348-2020/SC2348-2021 (médica),
SC496-2023 (producto), SC434-2023 (daño emergente/lucro cesante).

> ⚠️ **Regla dura:** ninguna sentencia entra al corpus sin que un abogado la
> verifique en la **Relatoría de la Sala de Casación Civil**. El modelo NUNCA
> inventa números de providencia.

**Doctrina (10 libros):** Devis Echandía, Parra Quijano, López Blanco, Bejarano,
Azula (probatorio/procesal); Tamayo Jaramillo, Henao "El daño", Hinestrosa,
Valencia Zea, Suescún (R. civil/obligaciones).
> ⚠️ **Copyright:** paráfrasis + cita, NUNCA reproducir párrafos completos. Para
> RAG interno sobre copias legales está bien indexar; la salida resume y cita,
> no transcribe.

## 9. Diseño del RAG

- **Alcance de ingesta inicial:** la tabla de prioridad (§8) + artículos núcleo
  del CC y la capa probatoria del CGP + fichas doctrinales. NO los códigos ni los
  libros completos por ahora.
- **Stack hackathon:** **Chroma** local (pip install, cero infra). Embeddings
  **multilingües** (calidad en español importa: p. ej. `multilingual-e5-large`
  local con sentence-transformers, o Voyage). Persistencia en disco.
- **Chunking:** por artículo (normas), por **tesis/problema jurídico** (sentencias),
  por ficha (doctrina). No chunks gigantes.
- **Metadata = trazabilidad (la estrella):** cada chunk con `id`,
  `problema_juridico`, `fuente_url`, `pagina/seccion`, `tipo` (norma/juris/doctrina).
- **Retrieval:** la cadena del Nivel 2 consulta por problema jurídico
  (p. ej. "actividad peligrosa causa extraña") → top-k chunks → cita por `id`.
- **Guardrail (criterio 5):** solo se cita lo que volvió del retriever; un
  validador comprueba que cada `cita_id` exista en el corpus y marca/elimina lo
  que no exista. Esto mata la alucinación de sentencias.

## 10. Roadmap (en orden de prioridad)

1. **Ingesta del corpus** → Chroma: tabla de prioridad (sentencias verificadas)
   + artículos núcleo + fichas doctrinales, con metadata. Ruta crítica.
2. **Retriever + validador de citas** (guardrail).
3. **Módulo 1** — esquema de extracción rica (extender `comprehension.py`).
4. **Cadena Nivel 2** — régimen → exoneración → perjuicio → terceros, cada etapa
   consultando el retriever para sus citas.
5. **Memo** — ordenado por solidez, con `cita_id` validadas → markdown.
6. **Wire** en `main.py` (`=== HOOK ===`).
7. **Front Next.js** — render de Módulo 1 (inventario, matriz, semáforo,
   click-to-source) + memo.

8. **`POST /ingerir`** — endpoint para subir documentos al RAG en vivo después
   del deploy en Render (no hay disco persistente en el tier gratuito; la BD
   ChromaDB se pierde en cada redeploy). Parámetros: `file` + `categoria`.

**Demo:** 1-2 casos de punta a punta (**producto o actividad peligrosa** lucen
mejor). Mostrar la **cadena visible** (razonamiento paso a paso) y el
**click-to-source** de las citas. Eso es lo que vende al jurado técnico.

## 11. Qué NO construir / cortar a slideware (DISCIPLINA DE ALCANCE)

La spec es una plataforma; para mañana, recorta. A **slideware** (lo dices como
"diseñado para", no lo construyes):
- Verificación externa de peritos por scraping (ReTHUS/COPNIA/RUES). Frágil, se
  rompe en vivo.
- Coordenadas pixel-exactas en escaneados. Página/folio/minuto basta.
- Autenticación, roles, cifrado, retención de datos (Ley 1581). Va en slides.
- Fórmulas de scoring ponderado exactas. Simplifica o fija.

NO construir nunca: agente autónomo, fine-tuning, OCR propio (Claude lee nativo),
cubrir los 4 tipos de demanda de forma robusta (1-2 basta), perfeccionar video
antes del núcleo, ingerir la biblioteca completa (solo la prioridad).

## 12. Convenciones técnicas

- Modelos: `claude-sonnet-4-6` para etapas (rápido, multimodal); opcional
  `claude-opus-4-8` para el memo final.
- PDF/imagen: bloques `document`/`image` base64 nativos, sin beta, sin OCR externo.
- RAG: Chroma + embeddings multilingües; metadata rica; persistencia en disco.
- Salida de cada etapa: JSON estricto (parser robusto en `comprehension._parse_json`).
- `ANTHROPIC_API_KEY` por env. Windows: `$env:VAR="..."` (PowerShell) o
  `set VAR=...` (CMD), no `export`.

## 13. División del equipo

- **Abogados:** contenido jurídico + corpus verificado (sentencias de la Relatoría,
  normas, fichas doctrinales con fuente comprobable).
- **Ingeniería (tú):** lectura (✓), RAG + guardrail, Módulo 1, cadena Nivel 2,
  front. Avanzan en paralelo sobre el contrato del esquema de salida (§6).
