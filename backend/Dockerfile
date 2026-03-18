FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend ./frontend
COPY backend ./backend
COPY artifacts/releases ./artifacts/releases

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
