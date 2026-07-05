# Setup config presentation + critique — Step 4 and Step 4b detail

Read this file before executing Step 4 (present detected config) and the
optional Step 4b (an in-repo quality-bar review of the detected config).

## Step 4: Present Detected Config

Show the user the assembled `.rawgentic.json` as formatted JSON. For each section, show where the values came from:

```
## Detected Configuration

### project (required)
Source: README.md + git remote
{
  "name": "my-app",
  "type": "application",
  "description": "A real-time monitoring dashboard"
}

### repo (required)
Source: git remote get-url origin
{
  "provider": "github",
  "fullName": "org/my-app",
  "defaultBranch": "main"
}

### testing
Source: vitest.config.ts, playwright.config.ts
{
  "frameworks": [...]
}

[... only sections where something was detected ...]
```

---

## Step 4b: Critique Detected Config (Optional)

After presenting the detected config in Step 4, compute a **complexity score** to determine whether to offer a multi-agent critique.

### Complexity Heuristic

Count these signals from the detected config:

| Signal | Condition | +1 if |
|--------|-----------|-------|
| Compose files | `infrastructure.docker.composeFiles` | length ≥ 3 |
| Infrastructure hosts | `infrastructure.hosts` | length ≥ 2 |
| Test frameworks | `testing.frameworks` | length ≥ 2 |
| Multi-env database | `database` exists AND multiple environments detected (e.g., dev/prod/test values in `.env*` files) | true |
| Deploy complexity | Multiple deploy methods detected (e.g., script + CI/CD, or ssh + compose) | true |

**Score interpretation:**
- **Score 0:** Skip critique entirely — proceed directly to Step 5.
- **Score 1:** Offer passively: *"Would you like me to run a critique on the detected config? (Optional — can catch missing capabilities)"*
- **Score ≥ 2:** Auto-suggest: *"This is a complex project (N complexity signals detected). I recommend running a multi-agent critique to validate completeness before you review. Run critique?"*

If the user declines (or score is 0), proceed to Step 5 with the config unchanged.

### Critique Execution

If the user accepts, apply the in-repo **quality-bar rubric** (`references/quality-bar.md` — skeptical single-pass review: cite evidence, don't rubber-stamp) to the detected `.rawgentic.json` as the work product, over these config-specific dimensions:

**Requirements coverage**
- For each schema section, check whether the detected config missed capabilities that exist in the actual project files
- Look for: test frameworks with config files but not detected, services with port mappings not captured, database references in `.env*` not reflected in config
- Check: are all Docker Compose services represented? Are all CI workflows captured?

**Structure & environment**
- Evaluate structural decisions: should services be split (e.g., frontend + backend vs monolith)?
- Check environment awareness: does the database config cover all environments?
- Validate infrastructure topology: do host assignments match actual deployment targets?
- Check service dependencies and port consistency across compose files

**Concrete-value verification**
- Verify every concrete value against source files: ports in config match ports in compose/code, paths exist on disk, container names match compose service names
- Check test commands actually work (correct binary, correct config file reference)
- Validate framework detection: does the detected framework version/type match the actual config file

The review produces findings:
```
Finding #N:
- Severity: Critical | High | Medium | Low
- Category: missing_capability | wrong_value | structural | environment | completeness
- Description: [what was missed or incorrect]
- Recommendation: [specific config change]
- Ambiguity flag: clear | ambiguous
```

### Applying Findings

1. **Auto-apply** all findings with `ambiguity_flag == "clear"` — amend the detected config in memory.
2. **Present ambiguous findings** to the user for resolution before proceeding.
3. **Re-present** the amended config with a summary of changes: *"Critique found N issues. Applied M automatically. K need your input:"*
4. Proceed to Step 5 with the amended config.
