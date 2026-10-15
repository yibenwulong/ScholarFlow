FROM python:3.13-slim-bookworm
WORKDIR /usr/src/app
ENV PYTHONUNBUFFERED=1 PIP_ROOT_USER_ACTION=ignore SCHOLARFLOW_DATA_DIR=/usr/src/app/data/scholarflow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libgdk-pixbuf-2.0-0 libffi-dev fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir ".[scholarflow]" \
    && useradd -m -u 10001 scholarflow \
    && mkdir -p data/scholarflow outputs logs \
    && chown -R scholarflow:scholarflow /usr/src/app
USER scholarflow
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
