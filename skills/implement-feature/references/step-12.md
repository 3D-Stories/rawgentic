## Step 12: Create PR and Push

### Instructions

1. **Wait for join barrier:** Both Step 10 and Step 11 complete.

2. **Include memorization changes:** If Step 10 updated CLAUDE.md, commit it:
   ```bash
   git add CLAUDE.md && git commit -m "docs: update CLAUDE.md with implementation insights (#<issue_number>)"
   ```

2a. **Update README + docs (mandatory decision, not optional):** Before pushing,
   explicitly decide whether this feature changed anything user-facing — new
   commands/flags, changed behavior, new files, or new config. If so, update
   `README.md` and the relevant `docs/` file(s) and commit them so they ship in
   this PR:
   ```bash
   git add README.md docs/ && git commit -m "docs: update README/docs for #<issue_number>"
   ```
   If there is genuinely no user-facing change (pure internal refactor, or
   groundwork that does nothing visible yet), state that explicitly in the PR
   body's Summary rather than silently skipping. Stale or omitted docs are a
   recurring miss — make the call deliberately every time.

2b. **HTML design artifact — create-or-update BEFORE the PR (opt-in, #174).** Same
   slot as the dashboard-before-PR rule. Config-gated — skip silently unless the
   project opts in (`is_enabled_for(..., 'implement-feature', key='designArtifact')`;
   exit 0 = enabled). **Target doc — shared vs per-issue:** read the `designArtifact.sharedDoc` config via
   `design_artifact_shared_doc('.rawgentic_workspace.json', '<name>')`. When it returns
   a path, use **shared-doc mode** — update THAT single rolling doc (the multi-issue /
   campaign model: one program doc updated per slot, like this repo's modernization
   dashboard), refreshing this issue's section, and do NOT create a per-issue file. When
   it returns None (default), use **per-issue** mode:
   `docs/planning/<issue>-<slug>.{md,html}`. Either way, create or update the `.md`+`.html`
   and commit BOTH inside THIS feature PR (one PR per issue; no trailing artifact commits).
   Render with the add-on's renderer —
   never hand-roll HTML — embedding this run's **telemetry** read from the run-record
   structure (Step 16's `/tmp/wf2-run-record-<issue>-<session-id>.json`; gate findings/resolved, tests +
   suite delta, security-scan, lane, `usage`), never hand-retyped.
   `<MD>` = `docs/planning/<issue>-<slug>.md`, `<OUT>` = `docs/planning/<issue>-<slug>.html`,
   `<TITLE>` = `#<issue> <title>`, `<TELEMETRY>` = `/tmp/wf2-run-record-<issue>-<session-id>.json`.
<render-addon>
Render through the **`design-doc-publish` add-on**, by ABSOLUTE path. A rawgentic skill runs
with the shell's cwd set to whatever project the session is bound to, so a repo-relative path
resolves only by accident — that was the bug (#807). The add-on is **suggested, not required**.

Substitute `<MD>`, `<OUT>`, `<TITLE>`, `<STYLE>` and `<TELEMETRY>` as **single-quoted shell
tokens**, rewriting every embedded `'` as `'\''`. Pass `''` for `<TELEMETRY>` when the caller has
no run-record to embed. Substitute nothing else — the heredoc is quoted, so the shell expands
nothing inside the program and the five values reach it as `argv`, never as shell words.

```bash
python3 - '<MD>' '<OUT>' '<TITLE>' '<STYLE>' '<TELEMETRY>' <<'RAWGENTIC_RENDER'
import errno, html, os, subprocess, sys, tempfile

md, out, title, style, telemetry = sys.argv[1:6]


def die(code, msg):
    print(msg, file=sys.stderr)
    sys.exit(code)


def stat_or(path, follow=True):
    """(st, errno) — never raises. errno is None on success."""
    try:
        return (os.stat(path) if follow else os.lstat(path)), None
    except OSError as e:
        return None, e.errno


# 1-2. Resolve the configuration root. An override must be absolute; a relative or
# empty one is a configuration error, never a silent fall-through to $HOME.
ccd = os.environ.get("CLAUDE_CONFIG_DIR")
explicit = bool(ccd)
if explicit:
    if not os.path.isabs(ccd):
        die(71, "config-error: CLAUDE_CONFIG_DIR is not an absolute path")
    root = ccd
else:
    home = os.environ.get("HOME")
    if not home or not os.path.isabs(home):
        die(71, "config-error: HOME is unset, empty, or not an absolute path")
    root = os.path.join(home, ".claude")

# 3-4. The root. A MISSING root splits on who chose it: an explicit CLAUDE_CONFIG_DIR
# pointing at nothing is a configuration error (the user named a place that is not
# there), but a default ~/.claude that does not exist just means there is no Claude
# configuration at all — and therefore certainly no add-on, which is ABSENT, not an
# error. Any OTHER errno is config-error either way: we cannot tell, and "cannot tell"
# must never be reported as "cleanly absent".
st, err = stat_or(root)
if err == errno.ENOENT and explicit:
    die(71, "config-error: CLAUDE_CONFIG_DIR points at %s, which does not exist" % root)
if err is not None and err != errno.ENOENT:
    die(71, "config-error: cannot read %s (errno %d)" % (root, err))
if err is None and not os.path.isdir(root):
    die(71, "config-error: %s is not a directory" % root)
root_missing = err == errno.ENOENT

addon = os.path.join(root, "skills", "design-doc-publish")
launcher = os.path.join(addon, "scripts", "render-doc")

# 5-6. Absent is reachable ONLY by ENOENT through a root already proven readable.
# A dangling symlink, EACCES, ELOOP or anything else is broken, never absent.
lst, err = stat_or(addon, follow=False) if not root_missing else (None, errno.ENOENT)
if err == errno.ENOENT:
    body = open(md, encoding="utf-8").read() if os.path.exists(md) else ""
    page = (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>%s</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:46rem;"
        "margin:3rem auto;padding:0 1rem}pre{white-space:pre-wrap;word-wrap:break-word}"
        ".b{background:#fdf2d0;border-left:4px solid #c90;padding:.75rem 1rem;margin-bottom:2rem}"
        "</style></head><body>\n<div class=\"b\">Rendered without the "
        "<strong>design-doc-publish</strong> add-on, which is not installed. This is a plain "
        "fallback page: the source is reproduced verbatim below, unstyled.</div>\n"
        "<h1>%s</h1>\n<pre>%s</pre>\n</body></html>\n"
    ) % (html.escape(title), html.escape(title), html.escape(body))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    die(20, "design-doc-publish is not installed; wrote a plain fallback page to %s" % out)
if err is not None:
    die(70, "broken: cannot read %s (errno %d)" % (addon, err))
if os.path.islink(addon):
    # The supported installation IS a symlink, so follow it — but a DANGLING one is
    # a half-installed add-on, which must fail loudly rather than look absent.
    _, err = stat_or(addon)
    if err is not None:
        die(70, "broken: %s is a dangling symlink (errno %d)" % (addon, err))
if not os.path.isdir(addon):
    die(70, "broken: %s exists but is not a directory" % addon)

st, err = stat_or(launcher)
if err is not None:
    die(70, "broken: add-on present but %s is unreadable (errno %d)" % (launcher, err))
if not os.path.isfile(launcher) or not os.access(launcher, os.X_OK):
    die(70, "broken: %s is not an executable regular file" % launcher)

# 7-8. Render to a temp file beside the destination; promote only after it validates,
# so a failed render can never clobber a good artifact.
dest_dir = os.path.dirname(os.path.abspath(out)) or "."
fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".html.tmp")
os.close(fd)
try:
    argv = [launcher, "--md", md, "--out", tmp, "--title", title, "--style", style]
    if telemetry:
        argv += ["--telemetry", telemetry]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        die(70, "broken: renderer exited %d\n%s" % (proc.returncode, proc.stderr.strip()))
    produced = open(tmp, encoding="utf-8").read()
    if not produced.strip():
        die(70, "broken: renderer produced an empty file")
    if not produced.lstrip().lower().startswith("<!doctype"):
        die(70, "broken: renderer output is not an HTML document")
    if html.escape(title) not in produced:
        die(70, "broken: renderer output does not contain the requested title")
    os.replace(tmp, out)
    tmp = None
finally:
    if tmp and os.path.exists(tmp):
        os.unlink(tmp)
RAWGENTIC_RENDER
```

**Exit codes — branch on these, never on the message text.** `0` rendered · `20` add-on absent
and a plain fallback page was written (say so out loud in your output) · `70` the add-on is
present but broken · `71` the configuration directory could not be determined. `70` and `71`
are failures: never treat either as the fallback.
</render-addon>
   Then stage the pair:
   ```bash
   git add docs/planning/<issue>-<slug>.md docs/planning/<issue>-<slug>.html
   ```
   **Style (#199, vocabulary expanded #344):** Design artifacts render with the
   template resolved by `design_artifact_style` — the full design-language vocabulary,
   defaulting to `design` when the config sets no style. Resolve `<style>` via
   `adversarial_review_lib.design_artifact_style('.rawgentic_workspace.json', '<name>')`
   → any of the seven template names (`plain`, `roadmap`, `report`, `design`,
   `dashboard`, `review`, `spec`); use `dashboard` for a campaign / roadmap-doc (h2
   sections rendered as bubble cards with completion chips). Pass it as `--style <style>`;
   an absent config key resolves to `design`, an invalid value to `plain` plus a stderr
   warning.
   Fields not knowable pre-PR (PR #, CI, merge SHA) follow the established
   convention: filled by the next slot's pass. Log
   `### WF2 Step 12 — design artifact #<issue> (updated|skipped)`.

3. **Final push:**
   ```bash
   git push origin <branch_name>
   ```

4. **Pre-PR test gate** (conditional):
   - If `capabilities.has_tests`: the full-suite evidence is the Step 9 run — re-run the full suite here ONLY when a commit landed after Step 9 touching code or a test-pinned surface (per `<test-run-discipline>`, SKILL.md); block the PR on any failure. **Prose-only scoped exception (#527):** when EVERY post-Step-9 commit touches ONLY prose/doc files (`*.md`, `docs/`) plus their own guard test files under `tests/` (no `hooks/`, no `scripts/`, no shared behavior code, and no shared test infrastructure — `conftest.py`, `tests/corpus.py`, cross-file test helpers), run the affected guard test files plus `tests/hooks/test_adversarial_review_registration.py` (the version pin) SCOPED and consume the Step 9 full-suite result as the regression evidence — log a session-note marker naming the scoped set (e.g. `#### Step 12 pre-PR gate: scoped (<files>)`); any code-bearing commit keeps the full re-run.
   - If NOT `capabilities.has_tests`: re-run key verification commands, document results

4a. **Review-completeness check:** before opening the PR, confirm Step 11's exit gate passed (item 8 — no unresolved Critical/High deferral) and every Step 8a covered task's verdict is `applied` or a persisted deferral. A suspend that never resolved must not reach PR creation.

4b. **Closing-keyword check (#901) — runs BEFORE `gh pr create`, never after.**
   GitHub's closing-keyword parser does not understand negation. A body sentence reading
   "this PR does not close #N" matches `close #N` and **shuts #N on merge anyway**. This has
   fired twice for real: issue #568 on the #573 merge (2026-07-21) and issue #874 on the #898
   merge (2026-08-04) — both on PRs that were deliberately `Part of`, with no closing keyword
   in any commit. The body prose alone did it.

   **The rule: never place a closing keyword — `close`, `closes`, `closed`, `fix`, `fixes`,
   `fixed`, `resolve`, `resolves`, `resolved` — adjacent to an issue number unless closure is
   intended.** When the issue must stay open, write **"leaves #N open"**. `Part of #N` and
   `Refs #N` are always safe. This binds the PR body AND the commit messages GitHub parses.

   Run the mechanical gate on the drafted body:
   ```bash
   python3 hooks/plan_lib.py check-pr-refs \
     --pr-body-file /tmp/wf2-pr-body.md \
     [--closes <issue this PR genuinely closes>]... --project-root .
   ```
   `0` no unintended closing reference · `1` FLAGGED — findings on stdout; rewrite the sentence,
   or add `--closes <n>` when the closure is genuinely intended · `2` caller error. **Omitting
   `--closes` means "this PR closes nothing", so every closing reference flags** — the gate
   fails toward asking, never silently. An empty `--pr-body-file` is rc 2 by design, never a
   pass. On a multi-PR issue only the LAST PR declares `--closes`; the earlier ones are
   `Part of #N` and must declare nothing.

5. **Create PR:**
   ```bash
   gh pr create \
     --repo ${capabilities.repo} \
     --title "<type>(scope): <description> (#<issue_number>)" \
     --body-file /tmp/wf2-pr-body.md
   ```

   PR body template:
   ```
   ## Summary
   [summary of changes]

   Closes #<issue_number>

   ## Design Decisions
   [key choices from Step 3]

   ## Verification
   [test results if available, or verification evidence]

   ## Deferred verification
   [ONLY when plan_lib.deferred_tasks(tasks) is non-empty; OMIT this whole
   section when empty. One bullet per deferred task, generated from the same
   verification_deferred list the run-record carries:
   - <task_id> (<reason>): <the exact manual check to run on the target — command/steps>]

   ## Quality Gate Summary
   - Design critique (Step 4): N findings
   - Plan drift check (Step 6): N findings
   - Implementation drift check (Step 9): N findings
   - Code review (Step 11): N findings (all Critical/High resolved)
   - Security scan (Step 11.5): N blocking resolved, N advisory, skipped: <kinds or "none">
   - <the `plan_lib.branch_protection_line(...)` string from Step 1 item 9 (#139) — states which layer enforces the gates>
   ```
   The `## Deferred verification` heading is the one canonical string (Step 9, Step 16, and `<completion-gate>` all key on it); do not reword it.

### Output
PR URL.

### Failure Modes
- Tests/verifications fail: fix and retry
- Push fails: retry; if persistent, save PR body locally
- Branch conflicts: rebase, resolve, re-push

---

