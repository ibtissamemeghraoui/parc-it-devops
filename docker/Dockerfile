
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# On copie d'abord les dépendances : Docker réutilise cette étape tant que
# requirements.txt ne change pas, ce qui accélère les reconstructions.
COPY app/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --only-binary=:all: --default-timeout=60 --retries 15 -r requirements.txt
RUN python -m pip uninstall -y pip
RUN apt-get update && apt-get install -y --only-upgrade zlib1g && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --only-upgrade zlib1g && rm -rf /var/lib/apt/lists/*

COPY app/ .

# Utilisateur sans privilèges : le conteneur ne tourne pas en root.
RUN useradd --create-home appuser && chown -R appuser /srv
USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
