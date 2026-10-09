FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 CONFIG_DIR=/config
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir $(grep -v -E '^(pytest|respx)' requirements.txt)
COPY app ./app
ARG VERSION=dev
ENV APP_VERSION=$VERSION
VOLUME /config
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
