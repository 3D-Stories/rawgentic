#!/usr/bin/env python3
"""Query structured JSONL session archives.

Usage: query-archive.py <archive_dir> [options]

Searches JSONL archive files for matching entries. Supports keyword search
(searches note text + enriched fields), structured queries (patterns,
decisions, artifacts from enriched insights only), date filtering, and
project filtering.

Exit 0: success, outputs JSON array of results
Exit 1: error, outputs JSON error object
"""

import argparse
import json
import re
import sys
from pathlib import Path


def load_entries(archive_dir: Path, project: str | None = None) -> list[dict]:
    """Load JSONL entries from archive directory.

    If project is specified, only reads <project>.jsonl.
    Otherwise reads all *.jsonl files.
    """
    if not archive_dir.is_dir():
        return []

    if project:
        files = [archive_dir / f"{project}.jsonl"]
    else:
        files = sorted(archive_dir.glob("*.jsonl"))

    entries = []
    for f in files:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _match_in_list(pattern: re.Pattern, items: list[str]) -> str | None:
    """Return the first matching item from a list, or None."""
    for item in items:
        if pattern.search(item):
            return item
    return None


def search_entries(
    entries: list[dict],
    *,
    keyword: str | None = None,
    pattern: str | None = None,
    decision: str | None = None,
    artifact: str | None = None,
    since: str | None = None,
) -> list[tuple[dict, str]]:
    """Search entries and return (entry, match_context) tuples."""
    results = []

    for entry in entries:
        # Date filter
        if since and entry.get("archived_at", "") < since:
            continue

        insights = entry.get("insights")
        note = entry.get("note", "")
        match_context = None

        if keyword:
            rx = re.compile(keyword, re.IGNORECASE)
            # Search note text
            if rx.search(note):
                for line in note.splitlines():
                    if rx.search(line):
                        match_context = line.strip()
                        break
            # Search enriched fields
            if not match_context and insights:
                summary = insights.get("summary", "")
                if rx.search(summary):
                    match_context = summary
                if not match_context:
                    for sess in insights.get("sessions", []):
                        found = _match_in_list(rx, sess.get("patterns", []))
                        if found:
                            match_context = f"pattern: {found}"
                            break
                        found = _match_in_list(rx, sess.get("decisions", []))
                        if found:
                            match_context = f"decision: {found}"
                            break

        elif pattern:
            rx = re.compile(pattern, re.IGNORECASE)
            if insights:
                for sess in insights.get("sessions", []):
                    found = _match_in_list(rx, sess.get("patterns", []))
                    if found:
                        match_context = f"pattern: {found}"
                        break

        elif decision:
            rx = re.compile(decision, re.IGNORECASE)
            if insights:
                for sess in insights.get("sessions", []):
                    found = _match_in_list(rx, sess.get("decisions", []))
                    if found:
                        match_context = f"decision: {found}"
                        break

        elif artifact:
            rx = re.compile(artifact, re.IGNORECASE)
            # Search enriched artifacts
            if insights:
                for sess in insights.get("sessions", []):
                    found = _match_in_list(rx, sess.get("artifacts", []))
                    if found:
                        match_context = f"artifact: {found}"
                        break
            # Fall back to note text
            if not match_context and rx.search(note):
                for line in note.splitlines():
                    if rx.search(line):
                        match_context = line.strip()
                        break

        if match_context is not None:
            results.append((entry, match_context))

    return results


def format_results(
    matches: list[tuple[dict, str]], fmt: str = "brief", limit: int = 10
) -> list[dict]:
    """Format matched entries for output."""
    # Sort by archived_at descending (most recent first)
    matches.sort(key=lambda m: m[0].get("archived_at", ""), reverse=True)
    matches = matches[:limit]

    output = []
    for entry, match_context in matches:
        if fmt == "brief":
            insights = entry.get("insights")
            if insights and insights.get("summary"):
                summary = insights["summary"]
            else:
                note = entry.get("note", "")
                summary = note[:100].replace("\n", " ").strip()

            output.append({
                "archived_at": entry.get("archived_at", ""),
                "source_file": entry.get("source_file", ""),
                "line_count": entry.get("line_count", 0),
                "summary": summary,
                "match_context": match_context,
            })
        else:  # full
            output.append({
                "archived_at": entry.get("archived_at", ""),
                "source_file": entry.get("source_file", ""),
                "line_count": entry.get("line_count", 0),
                "note": entry.get("note", ""),
                "insights": entry.get("insights"),
                "match_context": match_context,
            })

    return output


def main():
    parser = argparse.ArgumentParser(description="Query JSONL session archives")
    parser.add_argument("archive_dir", help="Path to archive directory")
    parser.add_argument("--keyword", help="Search note text + enriched fields")
    parser.add_argument("--pattern", help="Search insights patterns only")
    parser.add_argument("--decision", help="Search insights decisions only")
    parser.add_argument("--artifact", help="Search insights artifacts + note text")
    parser.add_argument("--project", help="Filter to specific project")
    parser.add_argument("--since", help="Filter by archived_at >= date (ISO 8601)")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--format", choices=["brief", "full"], default="brief", dest="fmt")

    args = parser.parse_args()

    # Require at least one search flag
    if not any([args.keyword, args.pattern, args.decision, args.artifact]):
        print(json.dumps({"error": "At least one search flag required: --keyword, --pattern, --decision, or --artifact"}))
        sys.exit(1)

    # Validate regex early
    search_term = args.keyword or args.pattern or args.decision or args.artifact
    try:
        re.compile(search_term)
    except re.error as e:
        print(json.dumps({"error": f"Invalid regex: {e}"}))
        sys.exit(1)

    archive_dir = Path(args.archive_dir)
    entries = load_entries(archive_dir, project=args.project)

    matches = search_entries(
        entries,
        keyword=args.keyword,
        pattern=args.pattern,
        decision=args.decision,
        artifact=args.artifact,
        since=args.since,
    )

    output = format_results(matches, fmt=args.fmt, limit=args.limit)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
