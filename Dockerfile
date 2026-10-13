FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TALOS_MODE=hosted
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.2,<4"
COPY core/ ./core/
COPY server/ ./server/
COPY web/ ./web/
COPY artifacts/ ./artifacts/
EXPOSE 8018
CMD ["sh", "-c", "python -m server.deployment && exec uvicorn server.api:app --host 0.0.0.0 --port ${PORT:-8018} --workers 1"]
