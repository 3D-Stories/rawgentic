# Security Guard Hook Design

**Date:** 2026-03-07
**Status:** Approved (revised after multi-agent critique)

## Problem

The official `security-guidance@claude-plugins-official` plugin detects dangerous code patterns (eval, innerHTML, etc.) in file writes but has a flawed blocking mechanism:

1. It blocks with `sys.exit(2)` (stderr-based) on first detection
2. It saves a dedup key so the same file+rule passes on subsequent attempts
3. Claude Code auto-retries on hook errors, so every block automatically succeeds on retry
4. Result: the "block" is just a delay -- it never actually prevents anything

## Solution

A new `security-guard.py` hook in the rawgentic plugin that:
- Uses the proper JSON stdout protocol (`permissionDecision: deny`) which does not auto-retry
- Supports a per-project exception list in `.rawgentic.json`
- Instructs Claude to present the user with options instead of retrying

## Architecture

```
Write/Edit/MultiEdit/NotebookEdit triggered
        |
        v
security-guard.py reads stdin JSON
        |
        v
Extract file_path, normalize to project-relative
(project root = directory containing .rawgentic.json)
        |
        v
Check content against security-patterns.json
(all matches collected, not just first)
        |
   No match --> allow (exit 0, empty JSON)
        |
   Match(es) found
        |
        v
Load .rawgentic.json -> securityExceptions
(if not found: treat as empty list, all matches block)
        |
   All matches have exceptions --> allow (exit 0, empty JSON)
        |
   Unexcepted match(es) remain
        |
        v
Output JSON to stdout:
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny"
  },
  "systemMessage": "..."
}
Exit 0.
        |
        v
Claude reads systemMessage, presents user with:
  1. "Please use a safer approach"
  2. "Add exception for [suggested glob]"
        |
        v
User chooses --> Claude either rewrites code OR
                  edits .rawgentic.json and retries
```

### Conflict detection (SessionStart)

A separate SessionStart hook (`hooks/security-guard-check.sh`) runs once per
session to check if the official `security-guidance@claude-plugins-official`
plugin is still enabled. If so, it outputs a systemMessage telling Claude to
inform the user and provide the disable command. This avoids repeated file
reads on every Write/Edit.

## Components

### 1. hooks/security-guard.py

PreToolUse hook (~200-250 lines Python) that:

- Parses stdin JSON (tool_name, tool_input, session_id)
- Extracts file path and content from tool_input (see Content Extraction below)
- Finds the nearest `.rawgentic.json` by walking up from file path (project root)
- Normalizes file path to project-relative (strips project root prefix)
- Loads patterns from `hooks/security-patterns.json`
- Checks content against ALL patterns, collecting all matches (not first-match-wins)
- Loads exceptions from `.rawgentic.json` -> `securityExceptions`
- Filters out matches that have a matching exception (rule + pathPattern via `PurePath.match()`)
- If unexcepted matches remain: outputs deny JSON with aggregated systemMessage
- On allow or any error: exits 0 with empty JSON (fail-open)

**Content extraction by tool type:**
- `Write`: check `tool_input.content`
- `Edit`: check `tool_input.new_string` only (not `old_string`, to allow removing dangerous patterns)
- `MultiEdit`: check concatenated `new_string` from all edits
- `NotebookEdit`: check `tool_input.new_source` (the cell content being written)

**Code structure** (thin I/O + pure functions for testability):

```
parse_input(stdin_text) -> dict                          # pure
load_patterns(path) -> list[Pattern]                     # I/O, thin
find_project_root(start_path) -> Optional[Path]          # I/O, thin
normalize_path(abs_path, project_root) -> str            # pure
extract_content(tool_name, tool_input) -> str            # pure
match_patterns(rel_path, content, patterns) -> list[Match]  # pure
load_exceptions(rawgentic_path) -> list[Exception]       # I/O, thin
filter_exceptions(matches, exceptions, rel_path) -> list[Match]  # pure
suggest_glob(rel_path) -> str                            # pure
format_deny(matches) -> dict                             # pure
main()                                                   # orchestrator
```

### 2. hooks/security-patterns.json

Externalized pattern definitions. Initially seeded from Anthropic's security-guidance plugin. Structure:

```json
[
  {
    "ruleName": "eval_injection",
    "source": "upstream",
    "type": "substring",
    "substrings": ["eval("],
    "wordBoundary": true,
    "reminder": "eval() executes arbitrary code and is a major security risk. Consider using JSON.parse() for data parsing or alternative design patterns that don't require code evaluation.",
    "suggestedGlobs": ["**/__tests__/**", "**/*.test.*", "**/*.spec.*"]
  },
  {
    "ruleName": "github_actions_workflow",
    "source": "upstream",
    "type": "path",
    "pathPattern": ".github/workflows/*.yml",
    "reminder": "Be aware of command injection risks in GitHub Actions workflows...",
    "suggestedGlobs": []
  }
]
```

Fields:
- `ruleName` -- unique identifier, used in exception matching
- `source` -- `"upstream"` (from Anthropic's plugin) or `"custom"` (user-added). The sync skill uses this to distinguish patterns during merge.
- `type` -- `"substring"` (content check) or `"path"` (file path check)
- `substrings` -- for substring type, list of strings to search for in content
- `wordBoundary` -- if true, the substring must NOT be preceded by an alphabetic character. This prevents `medieval(` matching `eval(`, `pickled_data` matching `pickle`, and `document.writeStream` matching `document.write`. Default: false for backward compatibility.
- `pathPattern` -- for path type, glob matched against the **project-relative** file path using `PurePath.match()`
- `reminder` -- human-readable explanation of the security risk
- `suggestedGlobs` -- common exception paths Claude can suggest to the user

### 3. Path normalization

All path matching (both security patterns and exceptions) operates on **project-relative paths**.

1. The hook finds the project root by walking up from the target file's directory to find the nearest `.rawgentic.json`
2. The absolute file path is stripped of the project root prefix: `/home/user/project/src/__tests__/foo.js` becomes `src/__tests__/foo.js`
3. All `pathPattern` values in both patterns and exceptions are matched against this relative path
4. Path matching uses `pathlib.PurePath.match()` which correctly handles `**` as recursive glob (unlike `fnmatch` where `**` is just a longer `*`)

If no `.rawgentic.json` is found (walk reaches filesystem root), the hook treats it as "no project root found" -- no exceptions are available, and path-based pattern matching uses the absolute path as fallback.

### 4. Exception format in .rawgentic.json

```json
{
  "securityExceptions": [
    {
      "rule": "eval_injection",
      "pathPattern": "**/__tests__/**",
      "addedBy": "user",
      "date": "2026-03-07"
    }
  ]
}
```

Fields:
- `rule` -- matches `ruleName` from security-patterns.json
- `pathPattern` -- glob pattern matched against project-relative file path using `PurePath.match()`
- `addedBy` -- always `"user"` (Claude adds after user approval)
- `date` -- ISO date when exception was added

If `.rawgentic.json` exists but has no `securityExceptions` key, treat as empty list (all pattern matches block).

### 5. systemMessage format

When blocking, the hook outputs a JSON object to stdout:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny"
  },
  "systemMessage": "<see below>"
}
```

The `systemMessage` content (for a single match):

```
SECURITY BLOCK: Rule 'eval_injection' triggered on file 'src/__tests__/unit/security-middleware.test.js'.

eval() executes arbitrary code and is a major security risk.
Consider using JSON.parse() for data parsing or alternative
design patterns.

DO NOT retry this operation. Instead, present the user with
these options:
1. "Please use a safer approach" - rework the code to avoid
   the flagged pattern
2. "Add exception for **/__tests__/**" - add a security
   exception to .rawgentic.json for this path pattern and retry

Suggested exception glob: **/__tests__/**
```

For multiple matches, all are aggregated into one systemMessage with
numbered sections.

**File path sanitization:** The `{filePath}` value in the systemMessage is
sanitized to only contain `[a-zA-Z0-9._/\-]` characters and truncated to
200 characters max. This mitigates prompt injection via crafted filenames.

The hook auto-suggests a glob by detecting common path segments:
- `__tests__`, `test`, `tests`, `spec`, `specs` -> `**/{segment}/**`
- `.github/workflows` -> `.github/workflows/**`
- Fallback: the project-relative file path itself

### 6. /rawgentic:sync-security-patterns skill

A rawgentic skill that:
1. Checks if `security-guidance@claude-plugins-official` plugin is installed (reads its `security_reminder_hook.py`)
2. Extracts pattern definitions (ruleName, substrings, reminders) from the Python source
3. Merges them into `hooks/security-patterns.json`:
   - New upstream patterns (by `ruleName`) are added with `"source": "upstream"`
   - Existing upstream patterns are updated (reminder text, substrings)
   - Patterns with `"source": "custom"` are preserved untouched
   - Removed upstream patterns are flagged for user review (not auto-deleted)
4. Reports what was added/updated/unchanged/flagged

### 7. hooks.json update

Add to rawgentic's existing PreToolUse hooks:

```json
{
  "matcher": "Edit|Write|MultiEdit|NotebookEdit",
  "hooks": [
    {
      "type": "command",
      "command": "${CLAUDE_PLUGIN_ROOT}/hooks/security-guard.py",
      "timeout": 5
    }
  ]
}
```

Add to SessionStart hooks:

```json
{
  "matcher": "startup|resume",
  "hooks": [
    {
      "type": "command",
      "command": "${CLAUDE_PLUGIN_ROOT}/hooks/security-guard-check.sh",
      "timeout": 3
    }
  ]
}
```

Note: Commands use single-quote wrapping around `${CLAUDE_PLUGIN_ROOT}` to
match existing rawgentic hook conventions. The Python script uses a shebang
(`#!/usr/bin/env python3`) so no `python3` prefix is needed.

## Conflict detection

The `hooks/security-guard-check.sh` SessionStart hook checks
`~/.claude/settings.json` once per session for:

```json
"security-guidance@claude-plugins-official": true
```

If found, it outputs a systemMessage:

> The official `security-guidance` plugin must be disabled for rawgentic's
> security guard to work correctly. Both plugins check the same patterns,
> but the official one uses a broken blocking mechanism that auto-retries,
> and running both causes a confusing double-block loop.
> Run: `claude plugin disable security-guidance@claude-plugins-official`

If `settings.json` is missing or malformed, the check is silently skipped.

## Error handling

The hook follows a **fail-open** policy for all error conditions. This is
appropriate because it is a security *advisory* tool, not a security
*enforcement* tool. Failing open means a bug in the hook never blocks
legitimate work.

| Error condition | Behavior |
|----------------|----------|
| stdin JSON parse failure | exit 0, allow |
| `security-patterns.json` missing or malformed | exit 0, allow (+ systemMessage warning) |
| `.rawgentic.json` not found (walk exhausted) | continue with empty exception list |
| `.rawgentic.json` missing `securityExceptions` key | treat as empty list |
| `.rawgentic.json` malformed JSON | exit 0, allow (+ systemMessage warning) |
| `settings.json` missing or malformed | skip conflict check |
| `PurePath.match()` raises on invalid pattern | skip that pattern/exception |
| Unexpected exception in main() | exit 0, allow |

## Prerequisites

- Disable `security-guidance@claude-plugins-official` before use
- `.rawgentic.json` should exist in the project root (hook works without it but no exceptions are available)

## Patterns (initial seed)

Carried over from security-guidance, with `wordBoundary` applied where needed
to reduce false positives:

| ruleName | Type | Triggers | wordBoundary | False positive prevention |
|----------|------|----------|:---:|---------------------------|
| eval_injection | substring | eval( | yes | Prevents `medieval(` match |
| new_function_injection | substring | new Function | no | Space-separated, low FP risk |
| child_process_exec | substring | child_process.exec, execSync( | no | Dotted/camelCase, low FP risk |
| react_dangerously_set_html | substring | dangerouslySetInnerHTML | no | Unique identifier, no FP risk |
| document_write_xss | substring | document.write( | yes | Prevents `document.writeStream` match |
| innerHTML_xss | substring | .innerHTML = | no | Dot-prefixed + space, low FP risk |
| pickle_deserialization | substring | import pickle, pickle. | yes | Prevents `pickled_data` match |
| os_system_injection | substring | os.system, from os import system | no | Dotted/import, low FP risk |
| github_actions_workflow | path | .github/workflows/*.yml, *.yaml | n/a | Path-based, no content FP risk |

Note: `pickle` patterns changed from bare `pickle` to `import pickle` and
`pickle.` to avoid matching variable names like `pickled_data`. Similarly,
`document.write` changed to `document.write(` to avoid matching
`document.writeStream`.

## Testing

The hook should have unit tests covering:

1. **Pattern matching** -- each pattern type with positive and negative cases
2. **Word boundary** -- `medieval(` does not match `eval(`, `pickled_data` does not match `pickle.`
3. **Path normalization** -- absolute to relative conversion
4. **Exception matching** -- rule + pathPattern combinations, including root-level paths
5. **PurePath.match edge cases** -- `__tests__/foo.js` vs `**/__tests__/**`, nested paths
6. **Multi-match aggregation** -- two patterns matching same file
7. **Content extraction** -- each tool type (Write, Edit, MultiEdit, NotebookEdit)
8. **Error handling** -- missing config files, malformed JSON, invalid patterns
9. **JSON output structure** -- verify `hookSpecificOutput` wrapper is correct
10. **File path sanitization** -- special characters stripped from systemMessage
