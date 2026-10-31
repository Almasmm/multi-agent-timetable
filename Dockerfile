# syntax=docker/dockerfile:1
# Multi-agent timetable generator: one Dockerfile, three stages.
#   deps    - Python virtualenv with the pinned runtime dependencies (cached layer)
#   test    - deps + dev tools + the whole repository; CI runs pytest/ruff inside it
#   runtime - slim image with the app only, non-root user, health check (default target)
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS deps
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -r requirements.txt
ENV PATH=/opt/venv/bin:$PATH

FROM deps AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY . .
# tests never call a real LLM and never start the 24/7 service
ENV TT_LLM=offline TT_AUTOPILOT=off
CMD ["python", "-m", "pytest", "-q"]

FROM base AS runtime
ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="multi-agent-timetable" \
      org.opencontainers.image.description="Multi-agent AI system that generates a university weekly timetable (CP-SAT + GA + LLM)" \
      org.opencontainers.image.source="https://github.com/Almasmm/multi-agent-timetable" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /app app
COPY --from=deps /opt/venv /opt/venv
COPY --chown=app:app timetable ./timetable
COPY --chown=app:app data/input.xlsx ./data/input.xlsx
RUN mkdir -p /app/runs /app/data/uploads && chown app:app /app/runs /app/data/uploads
ENV PATH=/opt/venv/bin:$PATH \
    APP_VERSION=${APP_VERSION} \
    TT_OUT=/app/runs \
    TT_AUTOPILOT=auto
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "timetable", "web", "--host", "0.0.0.0", "--port", "8000"]
