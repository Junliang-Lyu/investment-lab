# Public Lab API image. Build from the investment-lab root:
#   docker build -f deploy/api.Dockerfile -t investment-api:RELEASE_ID .
#
# Only public code and demo fixtures are copied. private-data/, .env and the
# personal rule file never enter the image (also excluded by .dockerignore and
# checked by backend/tests/test_deploy.py).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend/src \
    FIXTURES_DIR=/app/fixtures \
    EDGAR_CACHE_DIR=/data/edgar \
    LAB_DATA_DIR=/data/lab

WORKDIR /app

COPY deploy/requirements-api.txt /tmp/requirements-api.txt
RUN pip install -r /tmp/requirements-api.txt \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/edgar /data/lab && chown app:app /data/edgar /data/lab

COPY backend/src/investment_core backend/src/investment_core
COPY backend/src/investment_data backend/src/investment_data
COPY backend/src/investment_ai backend/src/investment_ai
COPY backend/src/investment_api backend/src/investment_api
COPY backend/prompts backend/prompts
COPY fixtures/rules/demo.yaml fixtures/rules/demo.yaml
COPY fixtures/demo_portfolios fixtures/demo_portfolios
COPY fixtures/example_theses.yaml fixtures/example_theses.yaml
COPY fixtures/evals fixtures/evals

USER app
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/api/health', timeout=3)"]
CMD ["uvicorn", "investment_api.app:app", "--host", "0.0.0.0", "--port", "8081", "--workers", "1", "--no-server-header"]
