# Upgrading to rawgentic v3.0.0

v3.0.0 is the one migration event that bundles the breaking changes accumulated
across the 2.x modernization program (#161): the six workflows deprecated at
v2.60.0 (#160) are now **removed**, and the 2.x restructure is final. If you are
on any 2.x version, read this page once, refresh the plugin cache, and you are
done — there is no config migration to run.

## What's gone (and what to use instead)

The six deprecation stubs are deleted. Invoking them no longer resolves.

| Removed skill | Was | Use instead |
|---|---|---|
| `/rawgentic:refactor` | WF4 Refactoring | File a `refactor(scope): …`-typed issue (`/rawgentic:create-issue`) + `/rawgentic:implement-feature` — WF2's small-standard lane + characterization-test TDD covers it with full review/security gates |
| `/rawgentic:update-docs` | WF7 Documentation | Docs-typed issue + `/rawgentic:implement-feature` |
| `/rawgentic:update-deps` | WF8 Dependency Update | Deps-typed issue + `/rawgentic:implement-feature` (WF2 Step 11.5 runs the SCA scan) |
| `/rawgentic:security-audit` | WF9 Security Audit | Built-in `/security-review` for the reasoning pass + `/rawgentic:scan` for the tool pass (same `hooks/security_scan.py` lib WF2 Step 11.5 uses) |
| `/rawgentic:optimize-perf` | WF10 Performance | Perf-typed issue + `/rawgentic:implement-feature` |
| `/rawgentic:create-tests` | WF12 Test Suite Creation | superpowers TDD skills + `/rawgentic:implement-feature` |

Evidence for the removals (#160 AC12): 12/12 run-records were WF2-only, zero
session-note traces, design-doc mtimes frozen since March; the stubs' STUB-FIRED
telemetry recorded no firings across the deprecation cycle. The old design docs
remain in `docs/design/` as history.

## What moved during 2.x (already live before v3.0.0)

No action needed — listed so a jump from an early 2.x lands oriented:

- **WF2/WF3 spines split** (#158/#159): SKILL.md is a short spine; per-step
  detail lives in each skill's `references/`.
- **WF9's tooling survived** as the standalone `/rawgentic:scan` skill (#160).
- **Bundled subagents** (#164): reviews/implementation dispatch through
  `rawgentic:rawgentic-reviewer` / `rawgentic:rawgentic-implementer`.
- **Opt-in features** shipped across 2.x (`modelRouting`, `peerConsult`,
  `adversarialReview` diff stage, `designArtifact`, whole-issue delegation) are
  unchanged; the version-aware setup prompt (#184) tells you when a future
  upgrade ships another one.

## Refresh the plugin cache

The cache is per-version; after your marketplace picks up 3.0.0:

1. Exit all sessions using rawgentic hooks.
2. ```
   claude plugin remove rawgentic@rawgentic && claude plugin install rawgentic@rawgentic
   ```
3. Start a new session.

## Config notes

- **No `.rawgentic.json` or workspace schema changes.** Nothing to edit.
- A removed skill name left in a workspace list — e.g. `"refactor"` in
  `adversarialReview.workflows` — is **inert**: the loader matches entries
  against the running skill's name, so unknown names simply never fire
  (fail-closed, verified in `hooks/adversarial_review_lib.py`). Clean it up at
  leisure or leave it.
- `modelRouting`, `peerConsult`, `designArtifact`, `setupPrompt`, and
  `headlessEnabled` are unaffected.
- Version-pinned tooling: anything asserting the 2.x version string (like a
  fork of the pin test) must move to `3.0.0`.

## Verify after upgrade

`/rawgentic:switch <project>` then invoke any active skill — the session-start
reconcile will have bumped its marker silently (3.0.0 ships no new
setup-requiring feature). If a removed skill is still listed in your client's
skill picker, the cache refresh above hasn't happened yet.
