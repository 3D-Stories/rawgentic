# Skill Test Transcript: rawgentic:new-project (add-second-existing-dir)

## Parameters
- **Working directory:** /tmp/rawgentic-newproj-test/add-second
- **Input argument:** ./projects/second-app (path format)
- **Existing workspace:** .rawgentic_workspace.json with first-app as active
- **Directory state:** ./projects/second-app already exists with files (.git/, package.json)

---

## Step 1: Parse Input

Input is a path: `./projects/second-app`

- **Name:** `second-app` (last segment of the path)
- **Path:** `./projects/second-app`

> Registering project: **second-app** at `./projects/second-app`

---

## Step 2: Check If Already Registered

Read `.rawgentic_workspace.json`. Current projects:

| Name      | Path                  | Active |
|-----------|-----------------------|--------|
| first-app | ./projects/first-app  | true   |

Searched for `second-app` by name and `./projects/second-app` by path: **not found**.

Continuing to Step 3.

---

## Step 3: Folder Check

**Path B: Directory already exists.**

Verified directory is accessible -- listed contents:
- `.git/` (git repository)
- `package.json`

> Found existing directory at `./projects/second-app`. I'll register it as-is.

Continuing to Step 4.

---

## Step 4: Create Workspace File If Missing

`.rawgentic_workspace.json` already exists. No creation needed.

Continuing to Step 5.

---

## Step 5: Register the Project

1. **Deactivated** `first-app` (set `active: false`).
2. **Added** new entry to `projects` array:

```json
{
  "name": "second-app",
  "path": "./projects/second-app",
  "active": true,
  "lastUsed": "2026-03-06T13:12:00Z",
  "configured": false
}
```

3. Wrote updated workspace file (full read-modify-write).

> Registered **second-app** as the active project.

---

## Step 6: Delegate to Setup

> Project registered. Now let's configure it.

**[DELEGATION NOTE]** Would invoke `/rawgentic:setup` here to detect the project's tech stack and generate `.rawgentic.json`. Delegation was skipped per test instructions.

---

## Final Workspace State

| Name       | Path                   | Active | Configured |
|------------|------------------------|--------|------------|
| first-app  | ./projects/first-app   | false  | true       |
| second-app | ./projects/second-app  | true   | false      |
