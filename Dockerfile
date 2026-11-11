FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 EINWATCH_DB=/app/data/einwatch.sqlite3
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 einwatch \
    && mkdir -p /app/data \
    && chown -R einwatch:einwatch /app
COPY --chown=einwatch:einwatch src ./src
USER einwatch
EXPOSE 8000
VOLUME ["/app/data"]
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
