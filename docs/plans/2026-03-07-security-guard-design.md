# Security Guard Hook Design

**Date:** 2026-03-07
**Status:** Approved

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
Write/Edit/MultiEdit triggered
        |
        v
security-guard.py reads stdin JSON
        |
        v
Check: is security-guidance@claude-plugins-official still enabled?
        |
   Yes --> systemMessage: "Disable the official plugin first"
        |
   No
        |
        v
Check content against security-patterns.json
        |
   No match --> allow (exit 0)
        |
   Match found
        |
        v
Load .rawgentic.json -> securityExceptions
        |
   Exception exists for rule + path --> allow (exit 0)
        |
   No exception
        |
        v
Output JSON: { permissionDecision: "deny", systemMessage: "..." }
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

## Components

### 1. hooks/security-guard.py

PreToolUse hook (~150 lines Python) that:

- Parses stdin JSON (tool_name, tool_input, session_id)
- Checks if the official security-guidance plugin is still enabled (reads ~/.claude/settings.json)
- Loads patterns from `hooks/security-patterns.json`
- Checks file content against patterns (substring match + path-based checks)
- Loads exceptions from the nearest `.rawgentic.json` (walks up from file path)
- Matches exceptions by `rule` name + `pathPattern` glob (using `fnmatch`)
- On block: outputs JSON with `permissionDecision: deny` + instructional `systemMessage`
- On allow: exits 0

### 2. hooks/security-patterns.json

Externalized pattern definitions. Initially seeded from Anthropic's security-guidance plugin. Structure:

```json
[
  {
    "ruleName": "eval_injection",
    "type": "substring",
    "substrings": ["eval("],
    "reminder": "eval() is a major security risk...",
    "suggestedGlobs": ["**/__tests__/**", "**/*.test.*", "**/*.spec.*"]
  },
  {
    "ruleName": "github_actions_workflow",
    "type": "path",
    "pathPattern": ".github/workflows/*.yml",
    "reminder": "Be aware of command injection risks in GitHub Actions...",
    "suggestedGlobs": []
  }
]
```

Fields:
- `ruleName` -- unique identifier, used in exception matching
- `type` -- `"substring"` (content check) or `"path"` (file path check)
- `substrings` -- for substring type, list of strings to search for in content
- `pathPattern` -- for path type, glob pattern to match against file path
- `reminder` -- human-readable explanation of the security risk
- `suggestedGlobs` -- common exception paths Claude can suggest to the user

### 3. Exception format in .rawgentic.json

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
- `pathPattern` -- glob pattern matched against file path using `fnmatch`
- `addedBy` -- always `"user"` (Claude adds after user approval)
- `date` -- ISO date when exception was added

### 4. systemMessage format

When blocking, the hook outputs:

```
SECURITY BLOCK: Rule '{ruleName}' triggered on file '{filePath}'.

{reminder text}

DO NOT retry this operation. Instead, present the user with these options:
1. "Please use a safer approach" - rework the code to avoid the flagged pattern
2. "Add exception for {suggestedGlob}" - add a security exception to
   .rawgentic.json for this path pattern and retry

Suggested exception glob: {suggestedGlob}
```

The hook auto-suggests a glob by detecting common path segments
(__tests__, test, spec, .github/workflows, etc.). Falls back to the
exact file path if no common segment is found.

### 5. /rawgentic:sync-security-patterns skill

A rawgentic skill that:
1. Reads Anthropic's latest `security_reminder_hook.py` from the installed plugin
2. Extracts pattern definitions (ruleName, substrings, reminders)
3. Merges them into `hooks/security-patterns.json`, preserving custom patterns
4. Reports what was added/updated/unchanged

### 6. hooks.json update

Add to rawgentic's existing PreToolUse hooks:

```json
{
  "matcher": "Edit|Write|MultiEdit",
  "hooks": [
    {
      "type": "command",
      "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/security-guard.py",
      "timeout": 5
    }
  ]
}
```

## Conflict detection

On every invocation, `security-guard.py` checks `~/.claude/settings.json` for:

```json
"security-guidance@claude-plugins-official": true
```

If found, it outputs a systemMessage instructing Claude to inform the user:

> The official `security-guidance` plugin must be disabled for rawgentic's
> security guard to work correctly. Both plugins check the same patterns,
> but the official one uses a broken blocking mechanism that auto-retries.
> Run: `claude plugin disable security-guidance@claude-plugins-official`

## Prerequisites

- Disable `security-guidance@claude-plugins-official` before use
- `.rawgentic.json` must exist in the project root (or ancestor directory)

## Patterns (initial seed)

Carried over from security-guidance:

| ruleName | Type | Triggers |
|----------|------|----------|
| eval_injection | substring | eval( |
| new_function_injection | substring | new Function |
| child_process_exec | substring | child_process.exec, execSync( |
| react_dangerously_set_html | substring | dangerouslySetInnerHTML |
| document_write_xss | substring | document.write |
| innerHTML_xss | substring | .innerHTML =, .innerHTML= |
| pickle_deserialization | substring | pickle |
| os_system_injection | substring | os.system, from os import system |
| github_actions_workflow | path | .github/workflows/*.yml, *.yaml |
