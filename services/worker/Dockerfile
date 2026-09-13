FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY services ./services
COPY packages ./packages
RUN pip install --no-cache-dir .
RUN useradd --system --create-home codegenome \
    && mkdir -p /var/lib/code-genome/mirrors \
    && chown -R codegenome /var/lib/code-genome
USER codegenome
