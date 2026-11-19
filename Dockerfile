FROM python:3.12-slim

WORKDIR /app

RUN useradd --create-home appuser

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY --chown=appuser:appuser acquisition_plans /app/acquisition_plans

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "acquisition_plans.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
