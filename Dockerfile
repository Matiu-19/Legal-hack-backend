FROM python:3.11-slim

# ffmpeg para video/audio
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Logs sin buffer (los print() aparecen en Cloud Run en tiempo real)
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium para renderizar los PDFs premium (HTML -> PDF con Playwright).
# --with-deps instala las librerías de sistema que Chromium necesita.
RUN python -m playwright install --with-deps chromium

COPY . .

EXPOSE 8000

# Hypercorn habla HTTP/2 cleartext (h2c) — necesario para que Cloud Run con
# --use-http2 acepte requests > 32 MB. uvicorn no soporta HTTP/2.
CMD ["sh", "-c", "hypercorn main:app --bind 0.0.0.0:${PORT:-8000}"]