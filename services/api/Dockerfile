FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini ./
COPY services ./services
COPY packages ./packages
COPY infra ./infra
RUN pip install --no-cache-dir .
RUN useradd --system --create-home codegenome
USER codegenome

EXPOSE 8000
