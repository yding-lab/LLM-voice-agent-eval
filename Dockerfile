# Evaluation service: the judge pipeline and API, without the LiveKit/telephony half.
#   docker build -t llm-voice-agent-eval .
#   docker run --rm -p 8000:8000 llm-voice-agent-eval          # keyless replay demo -> http://localhost:8000/docs
#   docker run --rm -p 8000:8000 -e ANALYSIS_PROVIDER=openai -e OPENAI_API_KEY=... llm-voice-agent-eval
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ANALYSIS_PROVIDER=replay

WORKDIR /app
COPY requirements-eval.txt .
RUN pip install -r requirements-eval.txt

# Code, scenarios, labels, tracked runs, and the sample calls' transcripts (audio excluded by .dockerignore).
COPY evalkit/ evalkit/
COPY caller/scenarios.yaml caller/scenarios.yaml
COPY evals/ evals/
COPY calls/ calls/

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "evalkit.api:app", "--host", "0.0.0.0", "--port", "8000"]
