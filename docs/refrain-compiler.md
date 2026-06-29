# refrain-compiler service

Wraps the canonical Python compiler as an HTTP service for the coherence-portal
(Option A sub-project #2). Stateless `compile in → IR out`; author-time only,
never on the runtime hot path. Trusted-network sidecar — **no auth; do not expose
publicly.**

## Run

    pip install ".[server]"
    uvicorn refrain.server:app --host 0.0.0.0 --port 8000

Or build the image:

    docker build -t refrain-compiler .
    docker run --rm -p 8000:8000 refrain-compiler

## Endpoints

- `POST /compile` — body `{ "refrain": "<source>", "sample_rate_hz": 250 }` →
  `{ ir_json, meta, errors }`. Compile errors return **200** with `ir_json: null`
  and a populated `errors[]` (located for resolve errors). Malformed request →
  **422**; emitted-IR schema failure (compiler bug) → **500**.
- `GET /healthz` — liveness.
- `GET /version` — compiler + supported IR/schema versions.

`meta` carries `refrain_version`, `ir_version`, `sample_rate_hz`, and
`content_hash` (`sha256:` over the canonical IR bytes). The portal must store and
serve those exact bytes verbatim for the program-signature integrity check.

## CLI (same compile path)

    refrain compile-json protocol.refrain --sample-rate 250        # prints IR-JSON
    refrain compile-json protocol.refrain --meta                   # prints compile metadata
