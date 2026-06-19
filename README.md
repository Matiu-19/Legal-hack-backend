# Reto 2 RCE — Capa de lectura (ingesta multimodal)

Recibe documentos (PDF digital o escaneado, imágenes, video, audio), entiende
los hechos y devuelve un JSON estructurado listo para la cadena de
razonamiento jurídica. Pensado para correr local detrás de un front en Next.js.

```
ingest.py         normalize(path) -> bloques de contenido para Claude
transcribe.py     transcripción de audio opcional (faster-whisper)
comprehension.py  bloques -> hechos jurídicos estructurados (JSON)
main.py           FastAPI: POST /analizar
```

## 1. Instalar

```bash
cd reto-rce-lectura
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Dependencias del sistema:
- **ffmpeg** — solo si vas a procesar **video o audio**.
  macOS: `brew install ffmpeg` · Ubuntu: `sudo apt install ffmpeg`
- **faster-whisper** (opcional) — para transcribir el audio del video.
  `pip install faster-whisper`. Si no está, el video igual se analiza por
  frames y solo se omite el texto del audio.

PDF e imágenes **no necesitan nada extra**: Claude los lee de forma nativa
(incluido el PDF escaneado, sin OCR aparte).

## 2. Configurar la API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# opcionales:
# export COMPREHENSION_MODEL=claude-sonnet-4-6
# export WHISPER_MODEL=small
```

## 3. Probar la ingesta sola (sin API)

```bash
python ingest.py ruta/al/demanda.pdf ruta/a/peritaje.jpg
# -> "N bloques generados: {'text': .., 'document': 1, 'image': 1}"
```

## 4. Correr la API

```bash
uvicorn main:app --reload --port 8000
```

Prueba:

```bash
curl -X POST http://localhost:8000/analizar \
  -F "files=@demanda.pdf" \
  -F "files=@peritaje.jpg" \
  -F "files=@accidente.mp4"
```

## 5. Consumir desde Next.js

Tipo del response:

```ts
type Rubro = { rubro: string; monto: string | null; soporte: string | null };

type Hechos = {
  resumen_factico: string;
  tipo_caso_probable: "transito" | "actividad_peligrosa" | "medica" | "producto" | "otro";
  partes: { demandantes: string[]; demandados_potenciales: string[] };
  hechos: { hecho: string; fecha: string | null; fuente: string }[];
  danos_alegados: { tipo: string; descripcion: string; fuente: string }[];
  pruebas_aportadas: { tipo: string; descripcion: string; fuente: string }[];
  peritajes: { materia: string; conclusion: string; fuente: string }[];
  cuantia: { monto_total: string | null; rubros: Rubro[] };
  vacios_o_dudas: string[];
};

type AnalizarResponse = {
  ok: boolean;
  archivos: string[];
  hechos: Hechos;
  memo: string | null; // null hasta conectar la cadena de razonamiento
};
```

Llamada:

```ts
async function analizar(files: File[]): Promise<AnalizarResponse> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  const res = await fetch("http://localhost:8000/analizar", {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}
```

## 6. Dónde se conecta lo de los abogados

En `main.py`, en el bloque marcado `=== HOOK ===`. La lectura entrega `hechos`
estructurado y con fuentes; la cadena (régimen → exoneración → perjuicio →
terceros → memo) consume ese JSON y llena el campo `memo`.
