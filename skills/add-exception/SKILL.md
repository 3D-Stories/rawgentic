---
name: rawgentic:add-exception
description: Add a guard exception to the project's .rawgentic.json interactively. Use when a WAL guard or security guard blocks a legitimate operation and you want to add a per-project exception. Accepts guard type (wal or security) and a rule name or file path.
argument-hint: guard type and rule/path (e.g., "wal ssh-prod" or "security eval_injection tests/helpers.js")
---

<role>
You are the rawgentic guard exception assistant. Your job is to help the user add per-project exceptions to `.rawgentic.json` when a guard blocks a legitimate operation. You validate inputs, show what will change, ask for confirmation, and write the exception.
</role>

# Add Exception -- `/rawgentic:add-exception`

Run through the steps below **sequentially**.

---

## Step 1: Parse Input

The user provides a **guard type** and a **rule name or file path**. Accept flexible input:

- **Explicit:** `wal ssh-prod` or `security eval_injection tests/helpers.js`
- **Natural language:** "except the eval rule for test files" or "stop blocking ssh-prod"
- **No argument:** Ask: "Which guard blocked you? (wal or security) And which rule or file?"

Extract:
- `guard_type`: one of `wal` or `security`
- `rule_name`: the guard rule that triggered the block
- `file_path` (security only, optional): the file path that was blocked

If the guard type is ambiguous, ask the user to clarify.

---

## Step 2: Load Project Config

1. Read `.rawgentic_workspace.json` to find the active project (same resolution as other skills: conversation context -> session registry -> workspace default).
2. Read `<project-path>/.rawgentic.json`.
   - **If missing:** Tell the user: "No project config found. Run `/rawgentic:setup` first." STOP.
3. Extract:
   - `protectionLevel` (default: `strict` if missing)
   - `guards.wal` (explicit WAL rule override array, if present)
   - `guards.security` (explicit security rule override array, if present)
   - `guards.securityExcludePaths` (path exclusion globs, if present)

---

## Step 3: Validate Rule Name

### Known WAL guard rules (12):

Look up the rule name in this table. The full list of WAL rule names is defined in the `PATTERN_NAMES` array in `hooks/wal-guard` (lines 69-82). The 12 rules are:

`ssh-prod`, `scp-prod`, `rsync-prod`, `docker-prod-operate`, `docker-prod-destroy`, `ansible-prod-mutate`, `kubectl-prod-operate`, `kubectl-prod-destroy`, `helm-prod-operate`, `helm-prod-destroy`, `terraform-prod-operate`, `terraform-prod-destroy`

Each rule blocks a specific remote operations category targeting the "prod" environment.

### Known security guard rules (10):

Look up the rule name in `hooks/security-patterns.json`. The `ruleName` field identifies each rule. The 10 rules are:

`eval_injection`, `new_function_injection`, `child_process_exec`, `react_dangerously_set_html`, `document_write_xss`, `innerHTML_xss`, `pickle_deserialization`, `os_system_injection`, `github_actions_workflow`, `github_actions_workflow_yaml`

Each rule blocks writes containing specific dangerous code patterns or targeting sensitive file paths.

### Validation logic:
- If `rule_name` is not in the known set for the given `guard_type`: tell the user which rules are valid and ask them to pick one.
- If the user gave a close misspelling (e.g., `eval-injection` instead of `eval_injection`): suggest the correct name.

---

## Step 4: Determine Exception Action

### WAL Guard Exception

Determine the currently active WAL rules:

1. **If `guards.wal` array exists** in `.rawgentic.json`: use that list directly.
2. **If no explicit override**: expand the current `protectionLevel` preset:
   - `sandbox` -> no rules active (empty set)
   - `standard` -> `scp-prod rsync-prod docker-prod-destroy ansible-prod-mutate kubectl-prod-destroy helm-prod-destroy terraform-prod-destroy`
   - `strict` -> all 12 rules

**Check if the rule is currently active:**
- If the rule is NOT in the active set: tell the user: "Rule `<rule_name>` is not currently active under your `<protectionLevel>` protection level. Nothing to except." STOP.
- If the rule IS active: compute the new active set = (current active rules) minus `<rule_name>`.

**Show the change:**
```
WAL Guard Exception
===================
Protection level: <level>
Removing rule: <rule_name>

Current active rules (N):
  [list]

New active rules (N-1):
  [list]

This will write an explicit guards.wal array to .rawgentic.json,
overriding the <level> preset for WAL guards.
```

### Security Guard Exception

For security guards, there are two exception types. Determine which one based on the input:

**Type A -- Path exclusion** (when `file_path` is provided):

Suggest a glob pattern using this logic (mirrors `suggest_glob()` in `hooks/security_guard_lib.py`):
- If the path contains a test directory segment (`__tests__`, `test`, `tests`, `spec`, `specs`): suggest `**/<segment>/**`
- If the path is under `.github/workflows`: suggest `.github/workflows/**`
- Otherwise: suggest the exact file path

Check if the glob is already in `guards.securityExcludePaths`. If so: tell the user it is already excepted. STOP.

**Show the change:**
```
Security Guard Path Exception
=============================
Rule that triggered: <rule_name>
Blocked file: <file_path>
Suggested glob: <glob>

Will add to guards.securityExcludePaths:
  Current: [list or "none"]
  Adding: <glob>
```

Ask the user: "Use the suggested glob `<glob>`, or enter a custom pattern?"

**Type B -- Rule deactivation** (when no `file_path` provided, or user requests full rule removal):

Determine currently active security rules (same logic as WAL: explicit `guards.security` array, or expand preset). Compute the new set with the rule removed.

**Show the change:**
```
Security Guard Rule Exception
=============================
Removing rule: <rule_name>

Current active rules (N):
  [list]

New active rules (N-1):
  [list]

This will write an explicit guards.security array to .rawgentic.json.
```

**Preset expansion for security guards:**
- `sandbox` -> no rules active
- `standard` -> `eval_injection new_function_injection child_process_exec react_dangerously_set_html document_write_xss innerHTML_xss`
- `strict` -> all 10 rules

---

## Step 5: Confirm

Ask the user: "Apply this change? (yes/no)"

**If no:** STOP. Tell the user they can re-run with different parameters.

**If yes:** Continue to Step 6.

---

## Step 6: Write Exception

Read `.rawgentic.json` (full read-modify-write):

**For WAL rule exception:**
- Set `guards.wal` to the new active rule array (from Step 4).
- If `guards` object does not exist, create it.

**For security path exception:**
- Append the glob to `guards.securityExcludePaths`.
- If `guards.securityExcludePaths` does not exist, create it as an array with the single glob.
- If `guards` object does not exist, create it.

**For security rule exception:**
- Set `guards.security` to the new active rule array.
- If `guards` object does not exist, create it.

Write the full file back.

---

## Step 7: Show Updated Config

Print the updated `guards` section from `.rawgentic.json`:

```
Updated guards configuration:
{
  "guards": {
    "wal": [...],
    "security": [...],
    "securityExcludePaths": [...]
  }
}
```

Tell the user: "Exception added. Retry your original operation -- it should now be allowed."
