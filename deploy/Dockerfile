FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
COPY scripts/serve_sceneactor.py /app/scripts/serve_sceneactor.py
RUN python -m pip install --no-cache-dir .

USER 65532:65532
EXPOSE 8377
ENTRYPOINT ["python", "-u", "/app/scripts/serve_sceneactor.py"]
