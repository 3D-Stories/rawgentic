# Spike #456 — effort vocabulary probe: codex `model_reasoning_effort` + GLM `reasoning_effort` value sets, mapping draft (U-5)

**Date:** 2026-07-17
**Status:** Spike complete — report-only, docs-only PR. Answers U-5 from
`docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md` (unmerged, PR #451; that
doc is NOT edited by this PR — the orchestrator applies the disposition delta at the end of this
report).
**Scope:** Determine the REAL accepted value sets for the two non-claude providers' reasoning-effort
knobs, cross-check against in-repo usage, and draft a normalized-to-native mapping recommendation.
No config or code changed.

---

## 0. Given (not re-probed)

- **claude** `--effort` CLI values: `low | medium | high | xhigh | max` — CLI 2.1.212, already
  probed per the issue text and reflected as F14 in the AC doc. Treated as CONFIRMED, not re-verified
  in this spike.

---

## 1. codex — `model_reasoning_effort` (AC #1)

### 1.1 In-repo evidence (read first, per the issue)

`phase_executor/src/phase_executor/adapters/codex_cli.py:28-31`:

```python
def build_command(model: str, cwd: str, *, effort: str = "high") -> list:
    return [
        "codex", "exec", "--json", "-m", model,
        "-c", f"model_reasoning_effort={effort}",
        "--ephemeral", "--color", "never", "-c", "project_doc_max_bytes=0",
        "-s", "read-only", "-C", cwd, "--skip-git-repo-check", "-",
    ]
```

Default effort is `"high"`. Only `low|medium|high` appear in repo usage before this spike — confirmed
also in `hooks/adversarial_review_lib.py:59-60`:

```python
_EFFORT_ALLOWED: Final[frozenset] = frozenset({"low", "medium", "high"})
_EFFORT_DEFAULT = "high"
```

with the guarding comment at `hooks/adversarial_review_lib.py:113-117`:

```python
def _coerce_effort_env(name: str, default: str) -> str:
    """Parse a reasoning-effort env var. Unknown/empty -> default (fail-safe).

    xhigh is deliberately NOT allowed: it is unsupported on the current default
    model (gpt-5.5) and would be a hard runtime error, silently failing the gate.
    """
```

**This comment's factual claim ("xhigh unsupported on gpt-5.5") is FALSIFIED by the live probe below
(§1.2) — see the finding called out in §1.4.**

### 1.2 CLI version (CONFIRMED)

```
$ codex --version
codex-cli 0.144.1
```

### 1.3 Live probes (CONFIRMED — this is the conclusive evidence)

**Invalid value, default model** (`codex exec --json -c model_reasoning_effort=banana --ephemeral
--skip-git-repo-check "Reply with exactly: PONG"`, exit 1):

```
{"type":"error","message":"{\n  \"type\": \"error\",\n  \"error\": {\n    \"type\": \"invalid_request_error\",\n    \"message\": \"[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] Invalid value: 'banana'. Supported values are: 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', and 'max'.\"\n  },\n  \"status\": 400\n}"}
```

→ **The Responses API's `reasoning.effort` param (`ReasoningEffortParam`) accepts exactly 7 values:
`none, minimal, low, medium, high, xhigh, max`.**

**Valid value, default model, `xhigh`** (same command, `model_reasoning_effort=xhigh`, exit 0):

```
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"PONG"}}
{"type":"turn.completed","usage":{"input_tokens":17536,"cached_input_tokens":9984,"output_tokens":6,"reasoning_output_tokens":0}}
```

**Valid value, default model, `max`** (same command, `model_reasoning_effort=max`, exit 0): identical
shape, `PONG`, exit 0.

**`max` explicitly against `-m gpt-5.5`** (`codex exec --json -m gpt-5.5 -c
model_reasoning_effort=max --ephemeral --skip-git-repo-check "Reply with exactly: PONG"`, exit 1):

```
{"type":"error","message":"{\n  \"type\": \"error\",\n  \"error\": {\n    \"type\": \"invalid_request_error\",\n    \"code\": \"invalid_value\",\n    \"message\": \"Invalid value: 'max'. Supported values are: 'none', 'minimal', 'low', 'medium', 'high', and 'xhigh'.\"\n  },\n  \"status\": 400\n}"}
```

→ **CONFIRMED: acceptance is PER-MODEL.** `gpt-5.5`'s own 400 error names its supported set as
`none, minimal, low, medium, high, xhigh` (no `max`) — it explicitly EXCLUDES `max` but explicitly
INCLUDES `xhigh`. The repo comment's claim that xhigh is unsupported on gpt-5.5 is wrong; `max` is
what gpt-5.5 actually rejects.

### 1.4 Schema cross-check: `codex debug models` (CONFIRMED — local schema source)

`codex debug models` renders "the raw model catalog as JSON" (`codex debug --help`). Parsed
`supported_reasoning_levels` per model (full JSON saved in scratch; this is the decisive local
schema source the issue asked for):

| model slug | default | supported levels |
|---|---|---|
| gpt-5.6-sol | low | low, medium, high, xhigh, max, ultra |
| gpt-5.6-terra | medium | low, medium, high, xhigh, max, ultra |
| gpt-5.6-luna | medium | low, medium, high, xhigh, max |
| **gpt-5.5** | medium | **low, medium, high, xhigh** (no max — matches §1.3's live 400) |
| gpt-5.4 | medium | low, medium, high, xhigh |
| gpt-5.4-mini | medium | low, medium, high, xhigh |
| gpt-5.3-codex-spark | high | low, medium, high, xhigh |
| codex-auto-review | medium | low, medium, high, xhigh |

Catalog and live 400 agree exactly for gpt-5.5. `xhigh` is universal across every catalog model;
`max` is available only on the gpt-5.6 family; `ultra` appears only in the catalog (sol/terra) and
was **never named in any live wire-level 400** across two different models probed here — it is
likely a separate UI/product-tier construct ("Maximum reasoning with automatic task delegation" per
its catalog description, consistent with the codex subagents feature) rather than a
`model_reasoning_effort` value. **Flagged as INFERRED, not confirmed** — nothing in scope here
required settling it, and no seat currently requests it.

### 1.5 Docs cross-check (CONFIRMED, external, cited)

- Prior verified-from-docs session memory (`~/.codex/memories/rollout_summaries/2026-07-13T01-26-26-pdFC-codex_cli_model_reasoning_effort.md`,
  captured 2026-07-13 via the official docs MCP): *"Supported values from the verified config
  reference are `minimal`, `low`, `medium`, `high`, and `xhigh`."*
- Live fetch this session, `https://developers.openai.com/codex/config-reference` (308 →
  `https://learn.chatgpt.com/docs/config-file/config-reference`): *"Adjust reasoning effort for
  supported models (Responses API only; `xhigh` is model-dependent)."* Accepted values listed:
  `minimal, low, medium, high, xhigh`.

Docs (both sources, four days apart) omit `none` and `max` — the live 400 errors above are more
authoritative (today, this exact install, wire-level) and are the values actually enforced.
**Docs corroborate 5 of 7; the live probe is the source of record for the full 7-value enum and for
per-model gating.**

### 1.6 codex — conclusion

**CONFIRMED, conclusive:** the wire-level `model_reasoning_effort` enum is
`none | minimal | low | medium | high | xhigh | max` (7 values), but **acceptance is per-model** —
only `low|medium|high|xhigh` is guaranteed across every catalog model; `max` requires a gpt-5.6-family
model. `none`/`minimal` were named in the live 400s but not independently probed as accepted (out of
scope — no seat in the AC doc's seat table requests sub-`low` effort).

---

## 2. GLM — `reasoning_effort` (AC #2)

### 2.1 In-repo evidence (read first, per the issue)

`hooks/adversarial_review_lib.py` invocation shape, `_glm_attempts` (~line 1524-1533):

```python
stream = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": prompt}],
    response_format={"type": "json_object"},
    max_tokens=16384,
    temperature=0.2,
    thinking={"type": "enabled"},
    # zhipuai has no named reasoning_effort arg — send via extra_body
    # (pinned; GLM-5.2's implicit default is MAX: slow + token-heavy).
    extra_body={"reasoning_effort": effort},
    stream=True,
)
```

`effort=REASONING_EFFORT` (line 1673 etc.), `model=GLM_MODEL` = `"glm-5.2"` by default
(`hooks/adversarial_review_lib.py:147`). **Confirmed by source grep:** no `reasoning_effort` symbol
anywhere in the installed `zhipuai` SDK tree (`.venv-bench/lib/python3.12/site-packages/zhipuai/`) —
the comment is accurate: this is purely an `extra_body` passthrough with zero client-side validation,
so the accepted set is whatever the live endpoint enforces.

### 2.2 SDK version (CONFIRMED)

```
$ .venv-bench/bin/python3 -c "import zhipuai; print(zhipuai.__version__)"
v2.1.5.20250725
```
(`.venv-bench/bin/python3` → symlink to system `/usr/bin/python3`; `zhipuai` is importable there
only inside the venv, per the venv's own `site-packages`, as documented.)

### 2.3 Live probe (CONFIRMED — this is the conclusive evidence)

Client construction mirrors `_load_glm_client` (`hooks/adversarial_review_lib.py:1467-1477`):
`ZhipuAI(api_key=<from ZHIPUAI_API_KEY>, base_url=<from ZHIPUAI_BASE_URL/GLM_JUDGE_BASE_URL or SDK
default>, timeout=...)`. Probe script: `/tmp/.../scratchpad/spike-456/glm_probe.py` (scratch only,
not committed). API key referenced by name only (`ZHIPUAI_API_KEY`, sourced from
`glm-judge.env`) — never printed.

**Invalid value** (`reasoning_effort="banana"`, model `glm-5.2`, same subscription endpoint the judge
uses):

```
ERROR APIRequestFailedError: Error code: 400, with error text {"error":{"code":"1210","message":"reasoning_effort must be one of: none, minimal, low, medium, high, xhigh, max"}}
```

→ **GLM's `reasoning_effort` accepts the IDENTICAL 7-value enum as codex:**
`none, minimal, low, medium, high, xhigh, max`.

**Valid value, `low`** (200 OK — request accepted, no parameter error; ran out of `max_tokens=64`
mid chain-of-thought since GLM-5.2 always reasons before answering, confirming the *parameter* was
accepted, which is what this AC needs):

```
OK, finish_reason: length
OK, usage: CompletionUsage(prompt_tokens=18, completion_tokens=64, total_tokens=82, completion_tokens_details={'reasoning_tokens': 61}, prompt_tokens_details={'cached_tokens': 0})
```

**Valid value, `max`** (200 OK, same shape — confirms the repo comment's claim that glm-5.2's
*implicit* default is `max` is at least consistent with `max` being a genuinely accepted value for
this model):

```
OK, finish_reason: length
OK, usage: CompletionUsage(prompt_tokens=18, completion_tokens=64, total_tokens=82, completion_tokens_details={'reasoning_tokens': 64}, prompt_tokens_details={'cached_tokens': 0})
```

### 2.4 GLM — conclusion

**CONFIRMED, conclusive:** `glm-5.2`'s `reasoning_effort` (sent via `extra_body`, no client-side
validation in the zhipuai SDK) accepts `none | minimal | low | medium | high | xhigh | max` — the
same 7-value enum as codex's wire-level `reasoning.effort`. Both `low` and `max` were live-confirmed
accepted for this exact model; `none`/`minimal`/`medium`/`high` were named in the 400 error but not
independently round-tripped (out of scope — same reasoning as codex §1.6).
**Per-model variance for GLM is UNCONFIRMED** — only `glm-5.2` was probed; no GLM equivalent of
`codex debug models` was found to cross-check other GLM model slugs.

---

## 3. Mapping table (AC #3 — recommendation, not config)

### 3.1 Headline finding

All three providers share the same 5 names for the normalized (claude) vocabulary. **No renaming is
needed anywhere — the mapping is identity by string for every level claude exposes:**

| Normalized (claude vocab, given) | claude `--effort` | codex `model_reasoning_effort` | GLM `reasoning_effort` |
|---|---|---|---|
| `low` | `low` | `low` (universal — every catalog model) | `low` (confirmed live for glm-5.2) |
| `medium` | `medium` | `medium` (universal) | `medium` (named in the valid-set error; not round-tripped) |
| `high` | `high` | `high` (universal) | `high` (named in the valid-set error; not round-tripped) |
| `xhigh` | `xhigh` | `xhigh` (universal — every catalog model incl. gpt-5.5) | `xhigh` (named in the valid-set error; not round-tripped) |
| `max` | `max` | `max` **(model-gated: gpt-5.6-sol/terra/luna only — confirmed rejected on gpt-5.5)** | `max` (confirmed live for glm-5.2 — also its implicit default) |

Codex additionally accepts `none`/`minimal` below claude's `low` floor — irrelevant to the mapping
since no normalized level maps below `low`.

### 3.2 The real risk this spike found: NOT vocabulary, PER-MODEL SUPPORT

The issue's framing ("a wrong map silently downgrades effort") assumed a naming mismatch across
providers. **That assumption is wrong — there is no naming mismatch.** The actual risk is narrower:
**a given codex *model* may not support the requested normalized level** (confirmed: `gpt-5.5`
rejects `max`). Sending an unsupported value is NOT silent on the wire — codex and GLM both fail
closed with an explicit 400 (`turn.failed` / `APIRequestFailedError`). **The silent-downgrade risk is
in code that catches that mismatch and clamps quietly** — see §3.4, a live instance found in this
repo today.

### 3.3 Recommended rule (recommendation only — no config changed here)

1. **Identity-map by name** for the 5 shared levels (§3.1) — no per-provider rename table needed.
2. **Gate at resolve time, per resolved model:** before dispatch, check the target model's supported
   levels (codex: `codex debug models` catalog, cacheable; GLM: needs its own capability probe/allowlist
   per model — not established here).
3. **On unsupported: step down the ordinal ladder** `max → xhigh → high → medium → low` to the
   nearest level the resolved model actually supports — **never** silently substitute without
   recording it, and never let an unsupported value reach the provider as a live 400 in a run that
   is supposed to be unattended.
4. **Record BOTH values on every Observation:** the originally-requested normalized level AND the
   actual native value sent — matching the AC doc's own §5b(4) synthesis point ("Effort mapping
   recorded twice — normalized policy value + requested provider-native value on every
   Observation"). This spike's findings are the concrete vocabulary that field needs; this report
   does not implement the field.
5. This is a **recommendation for the wiring epic's build phase**, not a config change — no seat
   table, adapter, or schema in this repo was edited by this spike.

### 3.4 Pre-existing flaw surfaced (not fixed here — docs-only spike; naming it per the standing
rule against laundering a known defect into a "convention")

`hooks/adversarial_review_lib.py:59-60` + `:113-133` (`_EFFORT_ALLOWED = frozenset({"low", "medium",
"high"})`, `_coerce_effort_env`) deliberately excludes `xhigh` with a comment claiming it is
"unsupported on the current default model (gpt-5.5)". **§1.3 falsifies this today**: gpt-5.5's own
live 400 error names `xhigh` as accepted (it rejects `max`, not `xhigh`). Practical effect: setting
`RAWGENTIC_ADV_REVIEW_EFFORT=xhigh` today is **silently clamped to `high`**, with only a `stderr`
print (`hooks/adversarial_review_lib.py:124-129`) — exactly the failure mode ("a wrong map silently
downgrades effort") the issue is guarding against, live, in the codex adversarial-review path, right
now. **Recommend a fast-follow issue**: widen `_EFFORT_ALLOWED` to at least `{low, medium, high,
xhigh}` (universal per §1.4) and correct/remove the stale comment; leave `max` gated behind the
per-model check in §3.3 since it is NOT universal. Not actioned in this PR (docs-only, no code
changes per the spike's own workflow rules).

---

## 4. What was NOT checked

- codex `none`/`minimal` and GLM `medium`/`high` were named in valid-set error messages but not
  independently round-tripped with a successful call (out of scope: no seat requests sub-`low`
  effort, and `medium`/`high` are already in production use via the existing `_EFFORT_ALLOWED` set).
- GLM per-model variance (only `glm-5.2` probed; no discovery endpoint found to enumerate other GLM
  model slugs' supported levels the way `codex debug models` does for codex).
- codex `ultra` (catalog-only, sol/terra) — never appeared in a live wire-level 400; treated as
  INFERRED-not-a-`reasoning.effort`-value, not settled conclusively (no seat requests it today).
- Whether the CLI's implicit default model (used when no `-m` is passed) is exactly one particular
  catalog slug — self-report was unreliable (`"gpt-5-codex"`, not a catalog slug) — INFERRED only
  from behavior (accepts both `xhigh` and `max`, consistent with the gpt-5.6 family) not confirmed by
  identity.

---

## 5. Evidence index (scratch, not committed)

All raw command outputs live under `/tmp/claude-1000/-home-rocky00717-rawgentic/11d19ee3-2abf-42e3-be34-6e45299fbc5a/scratchpad/spike-456/`:
`codex-invalid-effort.out`, `codex-valid-xhigh.out`, `codex-valid-max-default.out`,
`codex-max-gpt55.out`, `codex-default-model.out`, `glm-invalid-effort.out`, `glm-valid-low2.out`,
`glm-valid-max.out`, `glm_probe.py` (probe script, no secret embedded — reads `ZHIPUAI_API_KEY` from
env at call time only).

---

## AC-doc disposition delta

*(Applied by the orchestrator to `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md`
§6 — this PR does not touch that file or its branch.)*

Replace the U-5 bullet in §6 with:

> - **U-5** — **RESOLVED via spike #456 (2026-07-17), CONFIRMED live.** claude `low|medium|high|xhigh|max`
>   (given, CLI 2.1.212). codex's wire-level `model_reasoning_effort` (Responses API
>   `reasoning.effort`) accepts `none|minimal|low|medium|high|xhigh|max` — confirmed via two live 400
>   errors on codex-cli 0.144.1 naming the exact enum (one at the default model, one pinned to
>   `gpt-5.5`), cross-checked against the local `codex debug models` catalog and official docs
>   (`developers.openai.com/codex/config-reference`, mirrored at
>   `learn.chatgpt.com/docs/config-file/config-reference`). GLM's `reasoning_effort` (via
>   `extra_body`, zhipuai SDK v2.1.5.20250725, model `glm-5.2`, the live judge endpoint) accepts the
>   **identical** 7-value enum — confirmed via a live 400 (`"reasoning_effort must be one of: none,
>   minimal, low, medium, high, xhigh, max"`) plus two successful round-trips (`low`, `max`).
>   **The original assumption was wrong in a useful way: there is no cross-provider naming mismatch —
>   all 5 claude-normalized levels map 1:1 by string onto both codex and GLM.** The real gap is
>   **per-model support within codex** (confirmed: `gpt-5.5` rejects `max`, accepts `xhigh`; the
>   gpt-5.6 family accepts both) — GLM per-model variance is unconfirmed (only `glm-5.2` probed).
>   **Recommendation for the build phase:** identity-map the 5 shared names; gate the resolved
>   native value against the resolved model's supported set at dispatch time (codex: `codex debug
>   models`, cacheable; GLM: needs its own capability probe, not yet available); on unsupported, step
>   down the ordinal ladder (`max → xhigh → high → medium → low`) to the nearest supported level and
>   record BOTH the normalized and native values on the Observation (matches §5b(4)) — never a silent
>   pass-through. **Live pre-existing bug this spike surfaced** (unfixed, docs-only spike):
>   `hooks/adversarial_review_lib.py`'s `_EFFORT_ALLOWED = {low, medium, high}` deliberately excludes
>   `xhigh` on a now-falsified claim ("unsupported on gpt-5.5" — gpt-5.5's own 400 names `xhigh` as
>   accepted); a `RAWGENTIC_ADV_REVIEW_EFFORT=xhigh` override is silently clamped to `high` today with
>   only a stderr print — a live instance of the exact silent-downgrade risk U-5 was raised to
>   prevent. Recommend a fast-follow issue to widen `_EFFORT_ALLOWED` and correct the comment. Full
>   evidence: `docs/planning/2026-07-17-spike-456-effort-vocabularies.md`.
