FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY services ./services
COPY packages ./packages
COPY infra ./infra
RUN pip install --no-cache-dir .

EXPOSE 8000
