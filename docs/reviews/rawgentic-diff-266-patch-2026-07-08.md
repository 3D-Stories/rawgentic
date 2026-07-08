# Adversarial Review — .rawgentic-diff-266.patch

- Date: 2026-07-08
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 3 (Critical 0, High 1, Medium 1, Low 1)

## Summary

The change replaces four jq field extractions with one jq invocation whose output is eval'd into shell variables, and adds tests plus release metadata. The main risk is that the implementation and its tests do not actually prove the documented eval safety boundary for all jq-output shapes.

## Findings

### 1. [High] security · medium confidence — hooks/wal-lib.sh wal_parse_fields jq filter

> +    "WAL_TOOL_NAME=" + ((.tool_name // "unknown") | tostring | @sh),

The safety claim depends on @sh always emitting a single shell word, but the jq filter can run once per JSON input in a stream. If WAL_RAW_INPUT contains multiple valid JSON texts, jq will emit multiple WAL_TOOL_NAME/WAL_SESSION_ID/WAL_TOOL_USE_ID/WAL_CWD assignment groups and eval will execute the whole generated script. That makes duplicate or trailing JSON input affect the final parsed fields instead of being rejected or bounded to one event, which is a guard bypass for event identity fields.

**Recommendation:** Change wal_parse_fields to enforce exactly one input object before eval, for example by using jq -e with a filter that slurps one value and errors unless length == 1 and type == "object", or reject any jq output that does not contain exactly four assignment lines.

### 2. [Medium] correctness · high confidence — tests/hooks/test_wal_lib.py _parse_with_shim

> +        values = re.findall(r"END(.*?)END", stdout, flags=re.S)

The test harness parses echoed values with END delimiters, but hostile field values are allowed to contain arbitrary strings and can include END. A value containing END would corrupt the parsed test result, so the tests do not faithfully validate the safety/property they claim for arbitrary field values.

**Recommendation:** In tests/hooks/test_wal_lib.py, replace delimiter regex parsing with a robust serialization format, such as emitting each variable through Python json.dumps from the shell test or base64-encoding each value before assertion.

### 3. [Low] feasibility · high confidence — tests/hooks/test_wal_lib.py _make_counting_shim

> +        shim.write_text(
> +            f'#!/bin/bash\necho x >> "{count_file}"\nexec "{real_jq}" "$@"\n'
> +        )

The counting shim hard-codes /bin/bash. On systems where bash is not installed at that absolute path, the WAL tests fail before reaching the code under review, even though the production hook source is ordinary shell.

**Recommendation:** Change the shim shebang in _make_counting_shim to use /usr/bin/env bash, matching the test runner's bash dependency without requiring it at /bin/bash.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._