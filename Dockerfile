# refrain-compiler service (Option A sub-project #2).
# Stateless build-time compiler sidecar — author-time, off the runtime hot path.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[server]"

EXPOSE 8000
# Trusted-network sidecar: do NOT expose this port publicly.
CMD ["uvicorn", "refrain.server:app", "--host", "0.0.0.0", "--port", "8000"]
