FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN uv pip install --system --no-cache -r /app/requirements.txt
COPY backend/app /app/app
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
