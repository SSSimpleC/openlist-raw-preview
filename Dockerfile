FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY app /app

RUN mkdir -p /var/lib/raw-preview/cache /var/lib/raw-preview/tmp \
    && chown -R 65532:65532 /var/lib/raw-preview

USER 65532:65532
EXPOSE 5260

ENTRYPOINT ["python", "/app/server.py"]
