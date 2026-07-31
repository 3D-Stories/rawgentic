# #765 Design Bake-off — wiring map (what's wired, what's not)

Companion markdown to `2026-07-31-765-bakeoff-wiring-map.html` (the visual; hosted at
https://rawgentic-765-bakeoff-map.vercel.app). Owner-commissioned mid-run 2026-07-31.

## The verdict, first

**Not completely ready to run every child — exactly ONE issue away (#775).** The
head-to-head mechanics are real and live-proven (two models concurrent, glm judge,
winner's exact bytes retrievable, both candidates recorded). What's missing is run-ready
plumbing: the judge environment must be hand-prepared per session, and no real WF2 child
has used the round as its actual design mechanism yet. Per the owner's call, the bake-off
ships **disabled by default** behind `designBakeoff.enabled` (workspace project entry);
hardening to run-ready is #775.

## The flow (owner's definition of "working")

1. **Gate** — `bakeoff_policy.py design-round-enabled` (exit 0 = opted in; default OFF). WIRED.
2. **Brief in** — the Step-3 design brief via `--prompt-file`, with the run's identity
   (`--run-id wf2-<issue>-<session> --correlation-id <issue>-s3-design`, required). WIRED.
3. **Head-to-head** — gpt-5.6-sol (codex pool) vs claude-opus-5 (claude pool), genuinely
   concurrent; a raising candidate is isolated, never aborts the round. WIRED (live-proven
   ×2 on 2026-07-31: 8.3s/10.7s and 25.7s/26.8s, both candidates ok).
4. **Judge** — glm-5.2 scores anonymized, seed-shuffled drafts on the vendored design
   rubric; picks the winner. WIRED — but the judge env (API key + zhipuai SDK) is manual
   per session (#775 item 1).
5. **Winner used** — `--winner-out` writes the winner's exact bytes (the Step-8a wave
   caught the CLI discarding them; fixed same day). MECHANICALLY WIRED; no real child has
   consumed it as its design draft yet (#775 item 2).
6. **Both recorded** — raw record (full payloads, both candidates) → gitignored sink;
   sanitized record (models, timings, scores, usage, prompt/rubric hashes) →
   `--evidence-out`, committable, fail-closed guard-tested. WIRED; per-run naming
   convention pending (#775 item 3).
7. **Move on** — Step 3 continues with the winner; on judge failure the CLI exits 3
   fail-loud (interactive) or records a flagged degraded incumbent (headless). WIRED.

## The flag

`designBakeoff: {"enabled": true}` on the project's entry in `.rawgentic_workspace.json`
— or bare `true`. Absent/malformed/unreadable = **OFF** (fail-open-to-off; Step 3 keeps
its current mechanism and logs a one-line skip). Check:
`python3 hooks/bakeoff_policy.py design-round-enabled --workspace <ws> --project <name>`.

## Not wired yet (all in #775)

- Judge env self-bootstrap (source `~/.config/rawgentic/glm-judge.env`, zhipuai-capable
  interpreter; today the round must run under the workspace `.venv-bench`).
- First real opted-in child using the round end-to-end (winner bytes = the design draft).
- Per-run evidence naming + run-record hook (makes bake-off value measurable per child).
- The default-ON decision — explicitly owner-gated, brought with measured evidence.
