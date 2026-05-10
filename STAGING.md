# Refrain — staging directory

**This directory is not part of Coherence Workstation.** It is a complete seed for the standalone `refrain-lang/refrain` repository, staged here temporarily because the local environment cannot push directly to `github.com/refrain-lang/refrain`.

## What's here

The full intended structure of the future Refrain language repository:

```
_refrain/
├── README.md
├── LICENSE                 # Apache-2.0
├── pyproject.toml          # Python package config (lark, numpy, scipy)
├── docs/
│   ├── CONCEPT.md          # Motivation, vision, history
│   ├── SPEC.md             # Language reference v0.0r1
│   ├── TOUR.md             # Tutorial-flavored walkthrough
│   └── PRIMITIVES.md       # Standard library reference
├── examples/
│   ├── smr_cz.refrain
│   ├── othmer_ilf_t3t4.refrain
│   └── alpha_theta.refrain
├── src/refrain/
│   ├── __init__.py
│   └── grammar.lark        # First-draft Lark grammar (Phase 0 subset)
└── tests/
    ├── __init__.py
    └── test_grammar_smoke.py
```

## Why it's here

Refrain is being developed as a separate open-source language project at `github.com/refrain-lang/refrain`. The Coherence Workstation recorder will depend on the `refrain` Python package (editable install during development).

The local Claude Code environment for this CW session has no SSH binary and no GitHub HTTPS credentials, so it cannot push directly to `refrain-lang/refrain`. This staging directory is the workaround: scaffold here, push to CW, the maintainer migrates manually.

## Migration steps for the maintainer

Once the new repo is ready to receive content:

```bash
# Clone CW and the new repo
git clone git@github.com:peak-mind-llc/coherence-workstation.git
git clone git@github.com:refrain-lang/refrain.git

# Copy the seed into the new repo
cp -r coherence-workstation/_refrain/* refrain/
cp coherence-workstation/_refrain/.gitignore refrain/  # if needed

# Initial commit in the new repo
cd refrain
git add -A
git commit -m "Initial scaffold for Refrain v0.0"
git push -u origin main

# Then remove the staging dir from CW
cd ../coherence-workstation
git rm -rf _refrain
git commit -m "docs(refrain): remove staging dir; seed migrated to refrain-lang/refrain"
git push
```

After migration, the CW `docs/refrain/` directory (which still has the design docs) can be replaced with a thin pointer README pointing at the canonical home in `refrain-lang/refrain`.

## Note on signing

The initial commit in this seed was made *without* signing because the local signing service isn't configured for the new repo path. When migrating, the maintainer's local environment will sign normally.
