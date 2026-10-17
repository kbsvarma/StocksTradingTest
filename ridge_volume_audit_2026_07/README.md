# Ridge-volume audit (2026-07)

Research-only audit of the claimed Ridge-plus volume improvement. Nothing in
this directory is imported by the trading daemon or changes production state.

Run:

```bash
python audit_and_extend.py
python run_model_ensemble.py
python validate_ensemble.py
python validate_ensemble_causality.py
python run_clock_falsification.py
python reproduce_declared_ratio.py
```

The script writes its complete numeric ledger to `results/`.
The audit conclusions are in `AUDIT_REPORT.md`.
