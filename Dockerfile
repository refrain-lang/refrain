# refrain-compiler service (Option A sub-project #2).
# Stateless build-time compiler sidecar — author-time, off the runtime hot path.
FROM python:3.12-slim

WORKDIR /app
COPY . .
# Pin the numeric stack. IR-JSON filter coefficients — and therefore each
# artifact's content_hash — are SciPy-version-dependent (butter/sosfilt design).
# Pinning keeps rebuilt images byte-reproducible so a base rebuild can't silently
# shift stored/served IR hashes out from under the portal's compile-once cache.
# Bump deliberately (and re-bless golden hashes) rather than let it float.
RUN pip install --no-cache-dir ".[server]" "numpy==2.4.6" "scipy==1.17.1"

EXPOSE 8000
# Trusted-network sidecar: do NOT expose this port publicly.
CMD ["uvicorn", "refrain.server:app", "--host", "0.0.0.0", "--port", "8000"]
