FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV WHISK_BOT_MCTS_SIMS=64
ENV WHISK_BOT_TEMPERATURE=0.05
ENV WHISK_BOT_EPSILON=0.0
ENV WHISK_BOT_TOP_K=1

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend ./frontend
COPY backend ./backend
COPY artifacts/releases ./artifacts/releases
COPY ["local instructions.txt", "remote instructions.txt", "human_vs_bot.txt", "./"]

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
