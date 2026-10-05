"""Phase 2 fidelity measurement harness (standalone, read-only with respect to the app).

Runs the *existing* production pipeline (app.services.pipeline, app.geometry...) on fixtures,
procedural ground-truth meshes and real photos, and measures how closely the assembled kit
resembles the reference, stage by stage. Nothing in `app/` is modified: instrumentation is
done with a subclass / wrappers inside this harness process only.

Run:  .venv/bin/python -m fidelity.run            (from backend/)
Out:  fidelity/reports/phase2/ (results.json, results.csv, report.md, overlays/)
"""
