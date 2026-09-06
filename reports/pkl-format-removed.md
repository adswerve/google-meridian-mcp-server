# `.pkl` model support: removed, not reinstated

This records a deliberate capability removal made during the Meridian 2.0
upgrade: this server no longer loads pickle (`.pkl`) Meridian models. Only
Meridian's proto format (`.binpb`) is supported.

## What the drift harness found

`reports/drift/02-backend-precision.md` (the `v2.0-tf` -> `v2.0-jax` drift
report, spec §4) compared 330 real tool calls across both backends. Every
single call against the `national-revenue-pkl` fixture -- the one fixture
that exercised the loader's pickle branch -- flipped from a normal data
payload under `v2.0-tf` to a structural error envelope under `v2.0-jax`
(`/columns`, `/model_id`, `/rows`, etc. all "key removed"; `/error_code`,
`/message`, `/details` all "key added"). That is over 200 of the report's 237
FAIL rows, all attributable to the same root cause.

## Root cause

Meridian 2.0 on the JAX backend cannot run inference on any `.pkl` model
that was saved under TensorFlow -- which is every 1.x pickle checkpoint. It
fails with a `jax.errors.TracerArrayConversionError` inside
`meridian/model/transformers.py:99`.

- `load_mmm` (Meridian's pickle loader) is a bare `joblib.load`. It restores
  whatever tensor type was pickled -- a TensorFlow `EagerTensor` for every
  model saved under the TF backend -- regardless of which backend is
  currently active. Under JAX, those restored TF tensors reach code that
  expects JAX arrays, and the conversion fails.
- The proto loader (`meridian.schema.serde.meridian_serde.load_meridian`,
  used for `.binpb`) is immune: it reconstructs tensors via
  `backend.to_tensor`, which is backend-aware by construction.
- Meridian explicitly deprecates `save_mmm`/`load_mmm` with no
  backward-compatibility guarantee, and Meridian 2.0.0 offers no
  `to_backend` or rehydrate helper to convert an already-pickled model to
  the active backend's tensor type.

In short: `.pkl` support was never safe under a JAX-only server, and
upstream offers no supported path to make it safe.

## Decision

`.pkl` is consequently unsupported by this MCP server:

- `meridian/loader.py` loads `.binpb` only; a `.pkl` path (or any other
  extension) raises `domain.errors.UnsupportedModelFormatError`
  (`error_code: unsupported_model_format`) with a message that names the
  format, explains that Meridian 2.0 dropped `save_mmm`/`load_mmm`, and
  says to re-export to `.binpb`.
- `domain.models.ModelFormat` no longer has a `PKL` member; the local and
  GCS providers derive their supported-extension set from `ModelFormat`, so
  discovery no longer enumerates `.pkl` files at all.
- The `national-revenue-pkl` validation fixture (and the harness scaffolding
  that built and exercised it) is removed from the matrix: the fixture
  factory in `generate_validation_models.py` no longer produces it, and
  `scripts/validation/matrix.py`/`runner.py`/`fixture_probe.py` no longer
  reference it. The fixture *directory* under `models/_validation/` is left
  untouched on disk -- it is simply no longer enumerated.

No conversion or migration script was added anywhere in this repository, by
explicit product decision: existing `.pkl` models are out of scope for this
server going forward, and a user who has one must re-export it to `.binpb`
using their own tooling outside this codebase.

`AGENTS.md` and `.env.example` are updated to stop documenting `.pkl` as a
supported format.
