# Reports

Committed artifacts from the Meridian 2.0 upgrade. `docs/` is gitignored in
this repository and nothing under it is tracked, so anything the design
document calls a committed artifact lands here instead.

- `drift/` — the three baseline drift reports (spec §4) plus the Phase 7
  cloud-vs-local reports and the Phase 5 refit notes.
- `cross-backend-gate-removed.md` — Task 16: what the deleted cross-backend
  JAX gate in `live_validate.py` used to prove, why it cannot be repaired
  (only one backend remains after Task 15/D2), and what covers that ground
  now.
- `pkl-format-removed.md` — Task 18b: what the drift harness found (every
  `national-revenue-pkl` tool call flipped to an error under JAX), the
  `TracerArrayConversionError` root cause, and the decision to drop `.pkl`
  model support entirely (no conversion/migration script added).
- `geox-calibration-findings.md` — spec §9.1 spike.
- `skills-audit.md` — spec §9.2 audit.
- `weekly-optimization-grid-measurements.md` — spec §11.5 measurements.
