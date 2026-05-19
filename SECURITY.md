# Security policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** via one of:

- GitHub's private vulnerability reporting:
  https://github.com/refrain-lang/refrain/security/advisories/new
- Email: `security@refrain.dev` *(replace with your real address when
  the org email is provisioned)*

**Do not file public GitHub issues for security concerns.** A public
issue gives any attacker a head start before a fix is available.

## What we'll do

- Acknowledge your report within 7 days
- Investigate and confirm or reject within 30 days
- Coordinate a fix and disclosure timeline with you
- Credit you in the release notes (unless you ask us not to)

## In scope

Issues that materially affect Refrain's correctness or safety:

- **Cryptographic issues in research-mode sealed allocation** — sealed-
  token forgery, tampering, key-exposure paths, weaknesses in the
  protocol-hash construction. See `docs/RESEARCH-MODE.md` for the
  intended cryptographic guarantees.
- **Parser issues** that could be exploited via crafted `.refrain`
  files (e.g., resource exhaustion, code execution via Lark, denial
  of service).
- **Evaluator memory-safety or resource-exhaustion** issues that a
  malicious protocol could trigger.
- **Supply-chain issues** in our published artifacts (PyPI, GitHub
  Releases).

## Out of scope

- **Clinical efficacy** of any specific protocol. Refrain is the
  language and runtime; protocol design is the protocol author's
  responsibility.
- **Patient identification** from `.refrain` artifacts. Protocol files
  contain no patient data by design. If a host application embeds
  patient identifiers in a protocol artifact, that is a host concern,
  not a Refrain concern.
- **Vulnerabilities in upstream dependencies** (numpy, scipy, mne,
  pyxdf, lark, PyNaCl, etc.) that have their own disclosure channels.
  Please report those upstream; we'll pick up the fix.
- **Issues that only manifest in patched / forked Refrain.**

## Clinical-use disclaimer

Refrain is research software, not a medical device. See `README.md`
for the full disclaimer. Security issues that would matter only in
a specific clinical-deployment context should be reported to the
host application's vendor as well as to us — the clinical-product
vendor owns the deployment-shaped risk surface.
