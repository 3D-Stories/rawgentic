# Fixture provenance manifest

Classifier corpus fixtures (`claude-stderr-*.txt`) hold RAW bytes — provenance lives
HERE, never inside the fixture (an in-file header would alter the exact bytes classified
and hashed; #558 pass-4 F12).

| fixture | provenance | expected verdict (quota_detect v1) |
|---|---|---|
| claude-stderr-quota-5h.txt | synthetic — external-docs shape of the 5-hour usage-limit exit-1 (no genuine capture exists; #559 calibrates) | True |
| claude-stderr-quota-weekly.txt | synthetic — weekly usage-limit shape with reset time | True |
| claude-stderr-auth-expiry.txt | synthetic — claude-flavored OAuth expiry | False |
| claude-stderr-account-select.txt | synthetic — account-selection error | False |
| claude-stderr-network-fail.txt | synthetic — DNS/network failure | False |
| claude-stderr-throttle-429.txt | synthetic — API rate-limit (contains "rate limit"; must NOT match usage-limit conjunct) | False |
| claude-stderr-wrong-cwd-resume.txt | **real** — spike #455 capture (docs/planning/2026-07-17-spike-455-resume-mechanics.md:125), exit 1 | False |
| claude-stderr-upgrade-only.txt | synthetic — usage-limit language WITHOUT temporal recovery phrasing (#558 pass-1 F2) | False |
| claude-stderr-budget-trip.txt | **real** — `--max-budget-usd 0.01` live probe 2026-07-21 (claude 2.1.216): exit 1, EMPTY stderr, envelope `error_max_budget_usd` on stdout | False |

Pre-existing fixtures (`claude-envelope*.json`, `codex-events.jsonl`,
`zhipuai-response.json`, `kukakuka-observation.json`, `routing-audit.jsonl`) predate
this manifest; their provenance is their creating PRs.
