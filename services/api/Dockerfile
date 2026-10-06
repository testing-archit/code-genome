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
# Code Genome's own docs, answered from by the workspace assistant.
COPY README.md PRD.md ARCHITECTURE.md ML_SPEC.md API_SPEC.md DATA_MODEL.md DELIVERY_AUDITOR.md SECURITY.md TESTING_STRATEGY.md PILOT.md ./
COPY docs ./docs
RUN pip install --no-cache-dir .
RUN useradd --system --create-home codegenome
USER codegenome

EXPOSE 8000
