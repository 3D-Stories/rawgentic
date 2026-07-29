# R1 — whole suite + both lint lanes on merged main (UAT run 2, 2026-07-28)

HEAD = 3c819c3 feat(hooks): context-pressure trigger — a hook that cannot forget (#687) (#691)

## pytest tests/ -q

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
5840 passed, 21 skipped, 5 warnings in 164.40s (0:02:44)
rc=0

## pylint hooks/*.py (verbatim CI command)
Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)

## pylint tests/ (verbatim CI command)
Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)

