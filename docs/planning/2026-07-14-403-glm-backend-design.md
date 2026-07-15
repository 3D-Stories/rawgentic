# Design — #403: selectable GLM review/consult backend (gpt | glm | both)

Date: 2026-07-14 · Issue: #403 · Complexity: standard_feature (full spine — new dependency)
Status: rev 5 — GATE PASSED (4 review passes, 3 loop-backs, 38 findings adjudicated;
owner-confirmed pass 2026-07-14; see "Step 4 gate resolutions")

## Approach

**Selected: parallel backend functions sharing all pure plumbing (mirror-function design).**
Add a GLM invocation layer to `hooks/adversarial_review_lib.py` as sibling functions
(`run_glm_review`, `run_glm_consult`) mirroring the Codex ones, sharing every pure
component that is backend-agnostic (artifact IO, secret scan, nonce prompts, schemas,
validators, renderers, path builders). A thin dispatch layer in `main()` maps
`--backend {gpt,glm,both}` onto the run functions. No existing function signature
changes in a breaking way; every new parameter is keyword-with-default so the existing
test suite passes untouched.

Alternative considered — **backend-object abstraction** (a `Backend` protocol with
`gpt`/`glm` implementations, dispatch table): cleaner for a hypothetical third backend,
but reshapes `run_codex_review`'s tested surface now for a speculative future need
(YAGNI); the mirror-function design keeps the diff additive and the codex tests
byte-identical. Revisit if a third backend ever lands.

## File changes

1. **`hooks/adversarial_review_lib.py`** (the whole feature, ~+350 lines):
   - `BACKENDS: Final = ("gpt", "glm", "both")`.
   - `AdversarialReviewConfig` gains `backend: str = "gpt"`. `_coerce_config` reads
     `raw.get("backend")`: value in BACKENDS → kept; **absent → "gpt" silently**
     (back-compat); **present-but-invalid → CONFIG ERROR, no egress** (gate
     resolution F-E, amending AC1): the config coerces to a sentinel
     `backend="invalid"` carried on the dataclass, and **every entry point that
     RESOLVES the backend from config** refuses with exit 2 and a message naming the
     rejected value BEFORE any provider call — a typo'd `"glm5"` must never silently
     reroute the artifact to OpenAI. Precedence mechanics (pass-4): the engine's
     `review`/`consult` `--backend` defaults to **None** (absence detectable, never a
     literal `gpt`); on absence the engine resolves from `config.backend` and applies
     the sentinel refusal; an EXPLICIT valid `--backend` is the source and skips the
     config sentinel check entirely. The refusal is thus anchored at the
     config-resolving sites (`backend` subcommand + the review/consult
     config-fallback path), never blocking an explicit valid arg. The rejected raw
     value is PRESERVED on the dataclass as `backend_error_value` (pass-2 A5) so
     every diagnostic can name it — the `"invalid"` sentinel alone would lose it.
     Bool shorthand unchanged (backend "gpt").
   - New env-frozen constant `GLM_MODEL` from `RAWGENTIC_ADV_REVIEW_GLM_MODEL`
     (default `"glm-5.2"`). Shared constants reused: MAX_BYTES, TIMEOUT_SECONDS,
     MAX_RETRIES, BLOCK_SECRETS, REASONING_EFFORT (GLM accepts the same
     low/medium/high vocabulary via `extra_body={"reasoning_effort": ...}` —
     verified shape in rawgentic-next).
   - GLM prereq helpers: `glm_sdk_available()` (deferred — never imports at module
     load; pass-4: checks `importlib.metadata.version("zhipuai") >= 2.1.5`, not mere
     `find_spec` discoverability — an older install reports NOT ready with upgrade
     guidance naming the detected version),
     `glm_api_key()` (`ZHIPUAI_API_KEY` → `ZHIPU_API_KEY` → `GLM_API_KEY`; read at
     CALL time, not frozen at import — a key exported mid-session must work),
     `glm_base_url()` (`ZHIPUAI_BASE_URL` → `GLM_JUDGE_BASE_URL` → default
     `https://api.z.ai/api/coding/paas/v4`, the Coding Plan subscription endpoint,
     AC9; pass-4 validation: MUST be https, MUST NOT carry userinfo/query/fragment —
     a violating override is a config error, exit 2, no egress; a non-z.ai https host
     is allowed but is named in the egress consent notice via the A4 rule).
   - `_load_glm_client(timeout)` — deferred `from zhipuai import ZhipuAI`;
     `ZhipuAI(api_key=key, base_url=base_url, timeout=timeout)` (pass-2 A1: the
     constructor CARRIES the per-attempt timeout — this is timeout layer 1; omitting
     it would leave `next(stream)` able to block forever).
   - `_glm_chat(client, prompt, timeout, model, effort)` — ONE streamed completion:
     `client.chat.completions.create(model=model, messages=[{"role":"user","content":prompt}],
     response_format={"type":"json_object"}, max_tokens=16384, temperature=0.2,
     thinking={"type":"enabled"}, extra_body={"reasoning_effort": effort}, stream=True)`,
     accumulated by `_collect_glm_stream(stream, deadline)` (concat `choices[0].delta.content`).
     **Timeout is TWO-LAYER (gate resolution F-A, both reviewers):** (1) the client is
     constructed with an SDK/httpx-level timeout (`ZhipuAI(..., timeout=<per-attempt
     TIMEOUT_SECONDS>)`) so a stalled socket read RAISES instead of blocking the
     iterator forever — a per-chunk check alone cannot interrupt a blocked
     `next(stream)`; (2) the per-chunk wall-clock deadline additionally caps total
     attempt time when chunks keep trickling. Both layers feed the existing
     retry→`timeout` path. **Test-layer honesty (pass-2 A12):** an injected fake
     client has no httpx, so layer 1 is NOT fake-testable — CI exercises layer 2
     only, via a TRICKLING iterator (sleeps between chunks past the deadline; a
     literally-blocked fake `next()` would hang the suite). Layer 1 is verified by
     the pre-merge live smoke. Timeout semantics: **per attempt** (mirrors the codex
     subprocess contract). On retry, a partially received stream is DISCARDED
     entirely — chunks never combine across attempts.
     Streaming is REQUIRED (non-streamed thinking calls die "Connection error.",
     measured live, rawgentic-next #74). Temperature 0.2 (deterministic review;
     rawgentic-next's 0.8 was for score-distribution sampling, not wanted here).
   - Schema-in-prompt: GLM `json_object` is freeform (no strict-schema enforcement),
     so a `_schema_instruction(schema)` suffix is appended to the SAME nonce-fenced
     prompt from `build_prompt`/`build_consult_prompt`: "Respond with a single JSON
     object conforming to this JSON Schema: <json.dumps(schema)>". Parsing goes
     through a tolerant `_strip_json_fences(text)` (defense: model wraps output in
     triple-backtick json fences) then the EXISTING `_parse_codex_output` → `validate_findings` →
     `normalize_findings` (review) / `_parse_codex_proposal` (consult). Fail-closed:
     any parse/validate failure → `parse_error` status (AC4).
   - `run_glm_review(artifact_path, artifact_type, project_root, *, timeout=None,
     headless=False, artifact_text=None)` → `CodexResult` (dataclass reused as the
     generic result type; gains a `backend: str = "gpt"` field; `model=GLM_MODEL`):
     prereq (sdk → `not_installed`, key → `unauthenticated` — the existing status
     vocabulary maps 1:1, so CLI exit codes are unchanged) → `read_artifact`
     → secret scan + BLOCK_SECRETS → nonce prompt + schema suffix → retry loop
     (MAX_RETRIES; SDK/network exceptions and deadline → retry, then `timeout`/`error`)
     → parse/validate → `success`. **`artifact_text` (gate resolution F-G, tightened
     pass-2 A3):** when a caller passes preloaded `(text, truncated)`, only the FILE
     READ is skipped — **the secret scan + BLOCK_SECRETS check ALWAYS run inside
     every run function on whatever text it is about to send** (the scan is cheap
     regex; making it caller-skippable would let any direct caller bypass the
     hard-block). `both` mode still reads once; it just cannot skip the scan. Same
     keyword added to `run_codex_review`, `run_codex_consult`, AND `run_glm_consult`
     (pass-2 A8) — keyword-with-default, existing tests unaffected.
   - `run_glm_consult(artifact, project_root, out_path, headless=False, timeout=None,
     *, artifact_text=None)` — mirrors `run_codex_consult` incl. the
     **empty-proposal-marker guarantee** on every non-success path
     (`_write_empty_proposal`). **Both-mode consult `--out` (pass-2 A13):** under
     `--backend both`, gpt writes the exact requested `--out`; glm writes a `-glm`
     sibling before the extension (same rule as the findings sidecar) — two
     independent peer proposals, no clobbering. Consumers (WF13 present step; WF2
     Step 3 embedded synthesis) derive the EXPECTED proposal set from the exit code:
     exit 0 → both files must exist and parse (the empty-proposal-marker guarantee +
     per-backend write-failure rule make a missing expected file impossible without
     that backend counting failed); exit 5 → the successful backend's file. A missing
     expected proposal = that backend FAILED — logged loudly, never a silent
     single-output run (pass-3 clarification).
   - `prereq_status(headless=False, backend="gpt")` — keyword param, default preserves
     the existing signature/messages byte-for-byte. `glm` messages: install
     `pip install "zhipuai>=2.1.5"` (pinned floor — the verified call-shape version,
     gate resolution F-C); set `ZHIPUAI_API_KEY` (headless variant included).
     **`both` = DEGRADE-AND-WARN (gate resolution F-B/F-D, amending AC3):** ok iff
     **≥1** backend ready; the message always reports BOTH backends' named results
     (never collapsed); an unready backend is a loud warning, not a blocker. Only
     zero-ready fails. This matches the run's "one failing never aborts the other".
   - `egress_warning(secrets=None, backend="gpt")` — destination named per backend:
     gpt → OpenAI (Codex) [existing text unchanged]; glm → z.ai / Zhipu (**different
     provider and jurisdiction**) **plus the EFFECTIVE endpoint's sanitized
     scheme+host resolved from `glm_base_url()` at warning time (pass-2 A4)** — with
     an env-overridden base URL the consent notice must name the real destination,
     not a hardcoded one; both → both destinations (AC6).
   - `render_report_md` / `render_consult_md`: meta gains `backend`; Reviewer line
     renders `Codex (model …, reasoning effort …)` or `GLM (model glm-5.2, reasoning
     effort …)` (AC7). Absent meta key → existing Codex wording (back-compat).
     **Pass-4 byte-compat rule (mirrors the sidecar matrix):** a gpt single-backend
     report renders BYTE-identical to today — the backend-aware wording appears only
     in glm/both-mode reports; a golden test pins the legacy gpt report text.
   - `review_report_path(..., backend="gpt")` / `consult_report_path(..., backend="gpt")`:
     glm → **`<slug>-<date>-glm.md`** / **`peer-<slug>-<date>-glm.md`** (suffix AFTER
     the date — gate resolution F-K: a `-glm` infix before the date collides when an
     artifact's own slug ends `-glm`; after the date, a gpt review of `foo-glm.md`
     yields `foo-glm-<date>.md` while a glm review of `foo.md` yields
     `foo-<date>-glm.md` — disjoint by construction); gpt paths unchanged (AC5).
     Residual sidecar case (user passes an `x-glm.json` base on a both-run: glm
     sibling becomes `x-glm-glm.json`) is distinct within any single run and
     accepted as Low with this rationale.
   - CLI: `review` and `consult` gain `--backend {gpt,glm,both}` (default `gpt`).
     New subcommand `backend --workspace --project --key {adversarialReview,peerConsult}`
     prints the config-resolved backend. **Exit contract (pass-2 A9 — supersedes the
     draft's "exit 0 always"):** exit 0 + printed backend for a VALID, ABSENT, or
     disabled config (absent/disabled → `gpt`); exit 2 + the rejected value on stderr
     for a PRESENT-BUT-INVALID `backend` — the subcommand must never launder `"glm5"`
     into `gpt` (that would route around the F-E refusal and egress to OpenAI).
     **Embedded callers on subcommand exit 2: abort the review layer LOUDLY (log +
     skip, non-blocking for the host workflow) — never fall back to gpt.**
   - **`both` semantics (AC5):** run gpt first, then glm — independently; one failing
     never aborts the other. The artifact is **read ONCE in dispatch**; the secret
     scan + BLOCK_SECRETS check then runs **inside each run function** on the shared
     immutable text passed via `artifact_text` (idempotent — the second cheap scan is
     intentional, per A3's unbypassable-scan rule; only the file READ is deduplicated,
     because two reads could observe a mutated file differently — peer finding). Per-backend report paths printed on
     stdout prefixed `gpt: <path>` / `glm: <path>`; a failed backend prints
     `gpt: FAILED (<status>)` to stderr. **Exit codes (gate resolution F-B, amending
     the draft's 0-on-partial):** `0` = ALL selected backends succeeded; **`5` =
     PARTIAL** (≥1 succeeded, ≥1 failed — machine-distinguishable; skills and
     embedded callers treat 5 as success-with-loud-warning, presenting the
     successful report(s) and naming the failed backend); all failed → the primary
     (gpt) failure's exit class (2/3/4). A backend skipped by degrade-and-warn
     prereq counts as failed for the exit code (so a one-backend both-run exits 5,
     never a silent 0). **`--findings-json` sidecar under `both` (peer design, adopted):** gpt
     writes the EXACT requested path (sidecar FORMAT byte-compatible with today's
     consumer); glm writes a `-glm` sibling before the extension (`x.json` →
     `x-glm.json`, same collision + traversal guards via `resolve_sidecar_path`).
     glm-only mode writes the exact requested path. A sidecar is written only for a
     SUCCESSFUL validated result — never from a failed backend; **and a per-backend
     sidecar/report WRITE FAILURE fails that backend** (mirrors the engine's existing
     fail-closed write contract — exit 3 on report/sidecar write failure — applied
     per backend), so "successful backend with missing output" cannot occur (pass-3
     clarification). **Consumer contract under `both` (pass-2 A10 — the format is
     unchanged, the exit/multi-file handling is not):** the consumer derives the
     EXPECTED sidecar set from the per-backend statuses — on exit 5 it reads the
     successful backend's sidecar (which the write-failure rule guarantees exists)
     and continues with a loud warning naming the failed backend; on exit 0 it reads
     BOTH the exact path and the `-glm` sibling and merges (dropping the sibling
     would silently lose every glm finding at the highest-value gate). **Merge
     semantics (pass-4, deterministic):** provenance attached from FILE identity;
     normalize both lists; dedupe on `(evidence, location, category)` keeping the
     highest severity on collision; stable sort by severity rank, then backend, then
     original order. An expected sidecar that is missing/unreadable is treated as
     that backend having FAILED, never silently skipped. **Which backend succeeded is
     learned from the CLI's per-backend stdout status lines (`gpt: <path>` /
     `glm: FAILED (<status>)`) — the authoritative both-mode manifest; consumers
     never infer it from the exit code or file existence alone (pass-4; applies
     identically to the consult proposal set).** **Sidecar field matrix (pass-3):** gpt-only AND
     both-mode gpt exact-path files = byte-identical to today (no `backend` key);
     glm-only AND both-mode `-glm` sibling = carry `"backend": "glm"` per finding;
     gpt provenance during a both-mode merge is assigned by the consumer from FILE
     identity, never written into the legacy-format file.

2. **`skills/adversarial-review/SKILL.md`** — constants (backend modes, GLM env vars,
   both-mode report paths), backend-resolution step (config default via `backend`
   subcommand; an explicit VALID user `--backend` arg **skips config resolution
   entirely** — the arg is the source, so a corrupt config `backend` is fatal only
   when config IS the source; with no arg, subcommand exit 2 → STOP, never gpt),
   prereq step (`prereq --backend <b>`), egress step (per-backend destination),
   invoke step (`--backend`), present step (two reports under both), argument-hint.
   **Exit-code table amended (pass-2 A11):** `0` success · `2` prereq/config ·
   `3` error/timeout · `4` parse · **`5` PARTIAL (both-mode only): present the
   successful report(s), name the failed backend, do NOT stop** — the current
   "on any non-zero exit the review did NOT succeed" sentence gains the exit-5
   carve-out.
3. **`skills/peer-consult/SKILL.md`** — same shape of edits (incl. exit-5 carve-out
   and dual `--out` presentation under both).
4. **`docs/config-reference.md`** — `backend` field on both blocks.
5. **README** — feature text + Changelog entry (exact repo shape, incl. diagram
   decision + Suite old→new).
6. **Version ×3 surfaces** — minor bump (3.37.0 → 3.38.0):
   `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`,
   `test_plugin_version_bumped`.
7. **Tests** (`tests/hooks/…`) — new module `test_glm_backend.py` (or folded into the
   existing adversarial-review test file per its conventions — Step 5 decides from the
   inventory): config `backend` coercion incl. invalid shapes AND
   present-but-invalid-refuses-with-no-egress + `backend_error_value` preserved (F-E,
   A5); prereq per backend (fake `find_spec`, env keys) incl. both-mode
   degrade-and-warn (F-B/F-D); `run_glm_review`/`run_glm_consult` with injected fake
   client (success, malformed JSON, empty stream, **trickling-iterator deadline
   timeout** (F-A/A12 — layer 2 only; layer 1 is live-smoke-verified), missing
   SDK/key, secrets block incl. secrets-block-with-supplied-artifact_text (A3));
   fence-strip; report paths (`-glm` after date) + Reviewer line; egress wording
   (z.ai/Zhipu + effective sanitized endpoint named, A4); base-URL redaction
   (scheme+host only, F-I); CLI `--backend` + `backend` subcommand exit contract
   (0 valid/absent, 2 present-invalid, A9) + both-mode exit-code matrix (0/5/failure
   classes) + sidecar sibling + consult dual `--out` (A13) +
   gpt-sidecar-byte-identical (F-H). Red-before-green for AC4.
8. **`skills/implement-feature/references/steps.md`** (gate resolution F-F —
   embedded wiring): each embedded engine invocation (Step 3 peer consult argv,
   Step 11 diff-review argv) resolves the project's configured backend via the
   `backend` subcommand and passes `--backend <resolved>`; on subcommand exit 2,
   abort the review layer loudly (log + skip, non-blocking) — never default to gpt
   (A9); skill-dispatch call sites (Steps 4/6/8) need no argv change (the WF5 skill
   resolves config itself). Step 11 join rewritten per the consumer contract above
   (exit 5 + dual-sidecar merge, A10); Step 3 synthesis reads both proposals when a
   `-glm` `--out` sibling exists (A13).

Pass-2 A14 (scope correction): the draft's file 9 (`skills/fix-bug/references/steps.md`)
is REMOVED — WF3's only adversarial touchpoint is the Step 4 skill dispatch
`/rawgentic:adversarial-review <rca-path> plan`, which resolves config itself; WF3
has no raw-argv `review`/`consult` site to wire. Verified against
skills/fix-bug/references/steps.md:258 by the pass-2 reviewer.

## Configuration changes

- Workspace per-project blocks gain optional `backend`:
  `"adversarialReview": {"enabled": true, "workflows": [...], "backend": "both"}` —
  same for `peerConsult`. Absent → `gpt`.
- New env: `RAWGENTIC_ADV_REVIEW_GLM_MODEL` (default `glm-5.2`); consumed (not new):
  `ZHIPUAI_API_KEY`/`ZHIPU_API_KEY`/`GLM_API_KEY`, `ZHIPUAI_BASE_URL`/`GLM_JUDGE_BASE_URL`.

## Error handling and failure modes

- SDK missing → `not_installed` (exit 2, message: `pip install "zhipuai>=2.1.5"`).
- Key missing → `unauthenticated` (exit 2, message names `ZHIPUAI_API_KEY`; headless variant).
- Present-but-invalid config `backend` → config error, exit 2, NO egress (F-E).
- Stream stall → SDK read-timeout raises (client-level) OR per-chunk deadline →
  retry with full discard, then `timeout` (exit 3) (F-A).
- SDK/network/construction exception (incl. incompatible installed SDK version) →
  retry where transient, then `error` (exit 3) with version guidance.
- Malformed / non-JSON / schema-invalid output → `parse_error` (exit 4) — never
  fabricated findings (AC4).
- Secrets + BLOCK_SECRETS → `error` before any egress (AC6).
- `both`: independent execution; exit 0 all-succeeded, exit 5 partial (loud), all
  failed → primary failure class (F-B).
- Injected-fake tests cover every path; no network in CI.

## Security implications

- **New egress destination:** artifact text goes to z.ai/Zhipu when backend includes
  glm — named explicitly in the egress notice (jurisdiction differs from OpenAI).
  Warn-only (matches existing policy); BLOCK_SECRETS hard-block applies identically.
- Nonce-fenced prompt-injection defense preserved verbatim (same `build_prompt` /
  `build_consult_prompt`; the schema suffix is appended OUTSIDE the fence).
- API key read from env only; never logged, never in argv (SDK sends it in headers —
  no subprocess, `shell=False` irrelevant: no shell at all on the GLM path).
- **Endpoint stderr log is REDACTED (F-I):** only scheme + host of the resolved base
  URL is printed (userinfo and query stripped) — a user-supplied
  `https://user:token@host/?key=…` override must not leak into session notes or
  headless STATUS comments. Redaction test included.
- Report-only invariant unchanged.

## Platform / external dependencies

platform_apis:
- api: zhipuai.ZhipuAI().chat.completions.create(stream=True, thinking, response_format=json_object, extra_body.reasoning_effort) against the z.ai Coding Plan endpoint (glm-5.2)
  feasibility: verified via existing-call-site — projects/rawgentic-next/hooks/glm_batch_judge.py `judge_cells_sync` + `_load_sync_client`, live-smoke-tested against `https://api.z.ai/api/coding/paas/v4` (module docstring VERIFICATION STATUS, 2026-07-13): slug valid, streaming completes where non-streamed dies, json worksheets parse under thinking mode
  failure: fail-loud
  surface: SDK raises / deadline aborts; engine converts every path to a non-success CodexResult status → CLI exit matrix consumed by skills and embedded callers as — 2/3/4 STOP (failed review, fail-closed); 5 PARTIAL (both-mode only — present successful backend outputs, name the failure, continue); 0 full success (pass-2 A2)

Note: `zhipuai` is NOT installed on this host/CI. CI stays network-free
(injected-fake-client tests over the exact call/stream shapes). **Install lifecycle
(pass-2 A6):** this is a user-host plugin, not a deployed service — there is no
project dependency manifest to cite; the prereq gate IS the installation lifecycle,
exactly as the codex CLI install is for the gpt backend (GLM mode is unsupported
where the user cannot `pip install`, and the prereq message says so). **Gate
resolution F-C (owner decision): a PRE-MERGE LIVE SMOKE replaces the
deferred-to-target plan** — at Step 9 verification, `pip install "zhipuai>=2.1.5"`
locally, obtain the subscription key from the owner, and run one real
`--backend glm` review against the Coding Plan endpoint; only then may the PR claim
live-verified. Install guidance pins `zhipuai>=2.1.5` (the verified call-shape
floor). Fallback if the key is genuinely unreachable mid-run (pass-2 A7 wording):
the PR ships **explicitly NOT-live-verified** — deferred-to-target with the #138
recording discipline (plan task line, PR `## Deferred verification` section,
run-record), naming the likeliest-wrong claim (exact kwarg acceptance of
`thinking=`/`extra_body=` by the installed zhipuai version). A fallback PR must
never describe itself as live-verified.

## Backward compatibility

- Default backend `gpt` everywhere (config absent, CLI arg absent) — codex-only users
  byte-for-byte unaffected; zero behavior change without opt-in.
- No existing function's positional signature changes; new params keyword-with-default.
- Existing report paths, exit codes, and status vocabulary unchanged for gpt.
  **Sidecar in gpt single-backend mode is BYTE-identical** — the per-finding
  `backend` key is emitted only in glm/both outputs (F-H), so strict-schema or
  snapshot consumers of today's sidecar are untouched. Exit 5 is emitted ONLY under
  `--backend both` (a mode no existing caller can be in).
- `CodexResult` dataclass name retained as the shared result type (renaming would
  churn the tested surface for zero behavior).

## Refinements adopted from the cross-model peer consult (provenance)

Peer proposal: `docs/reviews/peer-rawgentic-peer-problem-403-2026-07-14.md` (Codex,
blind both ways). Independently convergent on the mirror-function architecture,
deferred import, injected-client tests, status-vocabulary reuse, `-glm` path suffix,
and per-backend egress. Adopted from the peer where it was better:

1. **Sidecar-sibling design** (replaces my union-sidecar): under `both`, gpt writes
   the exact requested `--findings-json` path (consumer byte-compat), glm a `-glm`
   sibling — no schema change for existing consumers.
2. **Read-once shared input:** the artifact READ happens once; both jobs get
   identical immutable text. (The secret scan runs inside each run function on that
   text — A3 made it unbypassable, superseding the peer's scan-once placement.)
3. **Retry hygiene:** a partially received stream is discarded entirely on retry —
   chunks are never combined across attempts.
4. **Timeout semantics defined:** TIMEOUT_SECONDS is **per attempt** (matches the
   codex subprocess semantics, where each retry gets the full timeout); the stream
   deadline enforces it chunk-by-chunk.
5. **`both` prereq does not collapse detail:** both named results reported, not a
   merged boolean message.
6. **Endpoint visibility:** the selected GLM base URL is printed to stderr at call
   time (never the key) — catches a token routed to the wrong endpoint. (Tightened
   by gate resolution F-I: scheme+host only.)
7. **Runtime SDK incompatibility caught:** importability passing does not guarantee
   call-shape compatibility — client construction and create() calls are wrapped;
   failures → `error` status with version guidance.

Rejected from the peer, with reason: the normalized result-envelope + adapter layer
(two backends do not earn a third abstraction; `CodexResult` gains a `backend` field
instead — revisit with a third backend, same trigger as the backend-object
alternative above). `_parse_codex_proposal` keeps its name (it is already
provider-neutral; renaming churns the tested surface).

## Step 4 gate resolutions (four passes — gate PASSED at pass 4)

**Pass 4 (rev 4 → rev 5, VERDICT: PASSED, owner-confirmed).** Self-review: 0 C/H
(second consecutive convergence), 1 Medium (`--backend` None-default mechanics).
Adversarial: 2H/6M — H1 dissolves (the per-backend stdout status lines ARE the
both-mode manifest; sentence added), H2 discarded with reason (httpx read-timeout
applies per socket-read op incl. streaming-body iteration — documented httpx
semantics; zhipuai is httpx-based; synthetic stall-server test disproportionate),
M-5 = fifth F-C re-litigation (stands). Six rev-5 amendments: stdout-manifest
sentence; `--backend` None default + refusal anchored at config-resolving sites;
deterministic merge algorithm; `importlib.metadata` version-floor prereq; gpt
single-backend report byte-compat golden rule; `glm_base_url` https/no-userinfo
validation. Loop-back budget fully spent (design 2/2, spec_tighten 1/2, global 3/3);
owner elected PASS over out-of-workflow escalation.

**Pass 1 (rev 1 → rev 2).** Self-review (opus reviewer, 5 findings) + adversarial
(Codex, 7), deduped to 10; `design` loop-back #1 consumed. User-resolved forks
(2026-07-14): F-B/F-D degrade-and-warn + exit 5; F-E invalid-backend refuses with no
egress (amends AC1); F-F embedded WF2 wiring in scope; F-C pre-merge live smoke.
Clear fixes: F-A two-layer timeout; F-G `artifact_text` read-once; F-H gpt-sidecar
byte-identical; F-I redacted endpoint log; F-K `-glm` suffix after date.

**Pass 3 (rev 3 → rev 4).** Self-review CONVERGED (0 C/H, 1 Medium wording; verified
all pass-2 tension points cohere). Adversarial: 3H/3M — the Highs dissolve under
engine-contract verification (per-backend write-failure fail-closed precludes the
missing-output scenarios) or re-litigate the decided F-C (third time; stands).
Owner elected one more full gate pass; the final global loop-back consumed via
`spec_tighten` (the findings' true class; counters: spec_tighten 1/2, global 3/3).
Rev-4 wordings: scan-once → scan-in-each-run-function reconciliation ×2; per-backend
write-failure sentence (review sidecar + consult proposal); sidecar field matrix;
explicit-arg-skips-config-resolution precedence clause.

**Pass 2 (rev 2 → rev 3).** Self-review (opus, 6 findings, all spec-tightening) +
adversarial (Codex, 9), deduped to 13; adversarial findings enter untagged so the
fold forced `design` loop-back #2 (counters: design 2/2, global 2/3). No new user
forks — resolutions follow pass-1 decisions: A1 constructor timeout; A2 surface exit
matrix; A3 unbypassable secret scan on supplied text; A4 effective-endpoint egress
notice; A5 `backend_error_value`; A8/A13 consult `artifact_text` + dual `--out`;
A9 subcommand exit contract + no-gpt-fallback on invalid; A10 Step 11 exit-5 +
dual-sidecar consumer; A11 SKILL.md exit-5 carve-out; A12 trickling-iterator test
honesty; A14 fix-bug steps.md removed from scope (no raw-argv site). Discarded with
reason: adversarial "cite a dependency manifest" (user-host plugin — prereq gate is
the lifecycle, mirroring the codex CLI prereq) and "make the live smoke non-waivable"
(re-litigates the user's F-C decision, which stands; fallback wording made honest
instead).

## Multi-PR assessment

Single PR. ~400 engine lines + skills/docs/tests — cohesive single-module feature;
splitting would strand a half-wired backend. (Embedded steps.md wiring rides the
same PR — it is what makes the config field non-inert.)
