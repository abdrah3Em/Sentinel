# python:3.12-slim, pinned by digest (see docs/DECISIONS.md D5)
FROM python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY . .
RUN mkdir -p /app/data && chmod 700 /app/data
EXPOSE 8080 8081 5020 5021
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import os,urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('SENTINEL_API_PORT','8080'), timeout=2)" || exit 1
CMD ["python", "-m", "sentinel.api.app"]
