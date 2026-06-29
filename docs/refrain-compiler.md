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
  `{ ir_json, ir_json_text, meta, errors }`. Compile errors return **200** with
  `ir_json: null` and a populated `errors[]` (located for resolve errors). Malformed
  request → **422**; emitted-IR schema failure (compiler bug) → **500**.
- `GET /healthz` — liveness.
- `GET /version` — compiler + supported IR/schema versions.

`meta` carries `refrain_version`, `ir_version`, `sample_rate_hz`, and
`content_hash` (`sha256:` over the canonical IR bytes).

**Integrity / `ir_json_text`.** `content_hash` covers `json.dumps(ir_json, indent=2)` —
the exact bytes in **`ir_json_text`**, *not* a re-serialization of the `ir_json` object.
A non-Python consumer (the Go portal, the browser editor) cannot reproduce Python's
serialization byte-for-byte (key order / unicode escaping / float formatting differ), so
to keep `sha256(bytes) == content_hash` valid it must **forward and store the
`ir_json_text` bytes verbatim** — use `ir_json` only for display/inspection. The
`refrain compile-json` CLI prints exactly these bytes.

## CLI (same compile path)

    refrain compile-json protocol.refrain --sample-rate 250        # prints IR-JSON
    refrain compile-json protocol.refrain --meta                   # prints compile metadata
