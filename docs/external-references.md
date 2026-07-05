# Reliable use of external skills & commands (#194)

Rawgentic gates sometimes want to rely on something that lives **outside this
repo** — a built-in/plugin skill (`/security-review`), a marketplace command
(`/code-review`), or a plugin. Two failure modes made that brittle:

- **The #162 trap.** A gate was wired to `/code-review` with the plugin
  uninstalled and *nothing checking* — so the gate silently did nothing and the
  campaign couldn't tell. A missing dependency must be a **visible skip**, never
  a silent pass.
- **Brittle cache paths.** Running an external command by its hard cache path
  (`…/<plugin>/unknown/commands/…`) breaks the moment the version dir changes.

`hooks/external_ref_lib.py` is the one primitive for both.

## 1. Probe before you rely (`probe`)

Before a gate depends on an external skill/command/agent, probe it:

```bash
python3 hooks/external_ref_lib.py probe --kind command --name code-review
```

Output is JSON: `{exists, path, marketplace, plugin, version, trusted, reason}`.

- `exists: false` → the caller **degrades to a visible skip** (log it, record it
  in the run-record / session notes as a skip — never treat it as a pass). The
  `reason` field is the human message to surface.
- `exists: true, trusted: false` → the skill/command is present but comes from a
  marketplace not on the trust-list — do not *execute* its content (see §3).
- `exists: true, trusted: true` → safe to rely on; `path` is the resolved cache
  path (version-independent lookup, so no hard-coded `…/unknown/…`).

## 2. Durable copy with refresh (`vendor_copy`)

When a gate needs to *run* an external command's content, vendor a durable local
copy so a cache refresh / uninstall can't yank it mid-run:

```bash
python3 hooks/external_ref_lib.py vendor \
  --src "<probe .path>" --name code-review \
  --state-dir .rawgentic-vendored --marketplace rawgentic
```

`status` in the JSON:
- `copied` — first time (or the local copy was missing)
- `unchanged` — source hash matches the manifest; nothing done
- `refreshed` — source hash changed; local copy replaced
- `vanished` — source is gone; the **stale copy is retained** and the caller is
  alerted (exit 2). A stale command beats no command; surface the alert.

Change detection is a **sha256 manifest** (`.rawgentic-vendored/manifest.json`),
so a copy refreshes only on a real content change. The state dir is
**gitignored** — an external command file is third-party prompt content, so a
committed copy would redistribute someone else's work.

**Copy-on-upgrade.** The natural refresh point is the plugin version change that
`hooks/post_update_reconcile.py` already detects (#184); a driver or the upgrade
path re-runs `vendor` for each command it depends on so the local copies track
the newly-installed source. (The first real consumer is #196 — reopening the
#162 post-PR `/code-review` gate through the GA Action.)

## 3. Trust-gate (`is_trusted`)

Executing an external command file runs its author's prompt content, so
`vendor_copy` **refuses** a marketplace not on the trust-list
(`UntrustedSourceError`; the CLI exits 1). The built-in trusted set covers the
marketplaces rawgentic legitimately depends on (`rawgentic`,
`claude-plugins-official`, `context-engineering-kit`, `openai-codex`,
`anthropic-agent-skills`, `rawgentic-memorypalace`). Extend per-machine with
`RAWGENTIC_TRUSTED_MARKETPLACES` (comma-separated) — only for sources you trust.
`probe` reports `trusted` but never refuses (probing is read-only); the gate is
at vendor/execute time.
