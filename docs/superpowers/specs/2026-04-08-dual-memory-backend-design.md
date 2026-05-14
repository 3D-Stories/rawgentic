# Dual Memory Backend Design

**Date:** 2026-04-08
**Status:** Draft (revised after party mode review)
**Scope:** Two repos — rawgentic plugin (HTTP stubs + hooks) and rawgentic-memory service (Python API server with dual backends)

## Problem

Rawgentic's current memory system (MEMORY.md index + markdown files + session archives) is keyword-searchable and human-readable, but lacks semantic search and cross-project discovery. MemPalace (github: milla-jovovich/mempalace) offers 96.6% recall via ChromaDB vector search and a spatial organizational structure (wings/rooms/halls/tunnels), but stores data in an opaque vector database.

Rather than choosing one, we implement both behind a shared abstraction so we can empirically determine which produces better results across recall accuracy, token efficiency, developer experience, and cross-project intelligence.

**Key finding from review:** Current rawgentic sessions run for weeks/months with compaction, rarely stopping. The wal-stop hook fires as few as 3 times across 371 tool operations (grocusave). Any memory system relying on session stop is fundamentally broken for this usage pattern. Additionally, the archive directory has never been created — the archival pipeline has never triggered.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Repository structure | Two repos: rawgentic (stubs) + rawgentic-memory (server) | Different tech stacks, independent release cycles, minimal coupling (4 HTTP calls) |
| Backend selection scope | Workspace default + per-project override | Matches existing `protectionLevel` pattern |
| Data strategy | Dual-write always, active backend serves context | Fair A/B comparison on identical data |
| Session data location | `~/claude_docs/` (user-level) | Memory is user-level, not workspace-level |
| Vector engine | ChromaDB for both backends | Isolates organizational structure as the variable |
| Primary ingestion trigger | PreCompact hook (not wal-stop) | Sessions run for weeks; wal-stop rarely fires |
| Server architecture | Persistent FastAPI daemon with lazy-start | Avoids cold-start overhead on every hook call |
| AAAK dialect | Skip | 84.2% vs 96.6% regression in raw mode |
| MemPalace install | pip library during comparison, proper plugin if it wins | Avoid premature plugin coupling |
| Web frontend | First-class comparison component for both backends | Addresses ChromaDB opacity; [memory-palace-web-frontend](https://github.com/tomsalphaclawbot/memory-palace-web-frontend) works with both backends since both use ChromaDB |
| Extraction strategy | Additive HTTP stubs alongside existing scripts, clean up later | Zero breakage during transition; old scripts keep working |
| Comparison method | Ground truth benchmark (objective) + user ratings (subjective) | Separate retrieval accuracy from presentation preference |

## Architecture Overview

```
rawgentic hooks (bash)
    |
    +-- precompact hook (primary ingest trigger)
    |       curl POST http://localhost:9077/ingest
    |
    +-- session-start (wake-up path)
    |       curl GET http://localhost:9077/wakeup?project=X
    |
    +-- wal-stop (final flush)
    |       curl POST http://localhost:9077/ingest
    |
    +-- /rawgentic:recall skill (on-demand search)
            curl POST http://localhost:9077/search
    |
    v
rawgentic-memory server (FastAPI, lazy-start, idle timeout)
    |
    +-- dispatcher (dual-write)
    |       +-- native backend -> ChromaDB index over archives
    |       +-- mempalace backend -> wings/rooms/drawers + KG
    |
    +-- comparison logging -> ~/claude_docs/comparison/
    +-- web frontend (serves both backends)
    +-- benchmark endpoint (ground truth queries)
```

All HTTP calls degrade gracefully. If server is unreachable, hooks return silently and the session works normally without memory context.

## Two-Repo Split

### rawgentic (existing repo: 3D-Stories/rawgentic)

Stays lean: bash hooks, SDLC workflow skills, WAL guards, session management. Memory-related changes are limited to:

- `memory_api_call()` helper in `wal-lib.sh`
- HTTP wake-up call in session-start Section 2b
- HTTP ingest call in precompact hook and wal-stop
- `/rawgentic:recall` skill (thin wrapper around `/search` endpoint)

### rawgentic-memory (new repo: 3D-Stories/rawgentic-memory)

Owns all memory infrastructure:

- FastAPI server with lazy-start and idle timeout
- Native enhanced backend (ChromaDB over archives)
- MemPalace backend wrapper
- Comparison framework + ground truth benchmarks
- Web frontend serving
- `~/claude_docs/memory/` and `~/claude_docs/comparison/`

### Integration contract

Four HTTP endpoints are the entire coupling surface:

| Endpoint | Method | Caller | Purpose |
|---|---|---|---|
| `/wakeup?project=X` | GET | session-start | Returns L0+L1 context (~170 tokens) |
| `/ingest` | POST | precompact, wal-stop | Accepts WAL entries + session notes |
| `/search` | POST | /rawgentic:recall | Semantic search with filters |
| `/healthz` | GET | lazy-start check | Server readiness probe |

## Section 1: ~/claude_docs/ Migration

### Current state

`<workspace_root>/claude_docs/` contains WAL, session registry, session notes, archives, and `.current_session_id`. Every hook resolves this relative to the workspace root via `wal_find_workspace()` in `wal-lib.sh`.

### Target state

```
~/claude_docs/
+-- .config.json                 # global memory/session config
+-- session_registry.jsonl       # all sessions, all workspaces
+-- .current_session_id
+-- wal/
|   +-- chorestory.jsonl
|   +-- ...
+-- session_notes/
|   +-- chorestory.md
|   +-- archive/
|   |   +-- chorestory.jsonl
|   +-- ...
+-- memory/                      # dual backend home (owned by rawgentic-memory)
|   +-- chromadb/                # native backend's vector store
|   +-- mempalace/               # MemPalace backend's palace
|   +-- frontend/               # web UI docker setup
|       +-- docker-compose.yml
|       +-- config/
|           +-- native.json
|           +-- mempalace.json
+-- comparison/                  # comparison logs (owned by rawgentic-memory)
    +-- ground_truth.jsonl
    +-- queries.jsonl
    +-- wakeups.jsonl
    +-- ingestions.jsonl
```

### Migration strategy

1. Add `claudeDocsPath` field to `.rawgentic_workspace.json` (defaults to `~/claude_docs/`)
2. Update `wal-lib.sh` to resolve `$CLAUDE_DOCS` from workspace config instead of relative path
3. One-time migration script: move existing `claude_docs/*` to `~/claude_docs/`, leave a symlink at old location for backward compat
4. Update all hooks that reference `claude_docs/`: `session-start`, `wal-context`, `wal-pre`, `wal-post`, `wal-stop`, `query-archive.py`
5. Remove symlink after one release cycle

### What stays in workspace root

`.rawgentic_workspace.json`, `projects/`, `_bmad/` -- project configuration is workspace-level. Session/memory data is user-level.

## Section 2: Memory Server Architecture

### Lazy-start pattern

```bash
# In wal-lib.sh:
memory_api_call() {
    local endpoint="$1"
    local data="$2"
    local port="${MEMORY_SERVER_PORT:-9077}"

    # Try the call
    response=$(curl -s --connect-timeout 1 \
        "http://localhost:${port}${endpoint}" \
        -X POST -d "$data" 2>/dev/null)

    if [ $? -ne 0 ]; then
        # Server not running -- start it
        python3 -m rawgentic_memory.server --port "$port" --timeout 4h &
        disown

        # Wait for ready (max 10s)
        for i in $(seq 1 20); do
            curl -s "http://localhost:${port}/healthz" >/dev/null 2>&1 && break
            sleep 0.5
        done

        # Retry the actual call
        response=$(curl -s "http://localhost:${port}${endpoint}" \
            -X POST -d "$data" 2>/dev/null)
    fi

    echo "$response"
}
```

### Server behavior

- **Lazy-start:** First hook call that fails to connect starts the server in the background
- **Idle timeout:** Server shuts itself down after configurable period (default 4h) of no requests
- **ChromaDB warm:** Loaded in-process on startup, stays warm for all subsequent calls
- **Graceful degradation:** If server fails to start, hooks return empty; session works normally

### API endpoints

```
POST /ingest
  Body: {"project": "...", "session_id": "...", "wal_entries": [...], "notes": "...", "source": "precompact|stop|timer"}
  Response: {"native": {"indexed": N}, "mempalace": {"drawers": N}}

GET /wakeup?project=X
  Response: {"text": "L0+L1 context...", "tokens": 170, "layers": ["L0", "L1"], "backend": "native"}

POST /search
  Body: {"query": "...", "project": "...", "filters": {}}
  Response: {"results": [...], "comparison": {...}}  # comparison only in comparisonMode

GET /healthz
  Response: {"status": "ok", "uptime": 3600, "backends": {"native": true, "mempalace": true}}

GET /report
  Response: comparison report text

POST /benchmark
  Body: {"ground_truth_file": "..."}
  Response: R@1, R@5, project accuracy per backend

GET /stats
  Response: per-backend document counts, last ingest time, index size
```

## Section 3: Memory Abstraction Layer

### Interface

```python
# rawgentic_memory/backend.py

class MemoryBackend(ABC):
    def ingest(self, session_data: SessionData) -> IngestResult
    def search(self, query: str, filters: SearchFilters) -> list[SearchResult]
    def wake_up(self, project: str) -> WakeUpContext
    def recall(self, topic: str) -> str
    def stats(self) -> BackendStats
```

### Data types

```python
@dataclass
class SessionData:
    session_id: str
    project: str              # rawgentic project name
    wal_entries: list[dict]   # INTENT/DONE/FAIL/STOP from WAL
    notes: str                # session notes markdown
    enrichment: dict | None   # extracted decisions/patterns/artifacts
    timestamp: str            # ISO 8601
    source: str               # "precompact" | "stop" | "timer"

@dataclass
class SearchResult:
    content: str              # the matched text
    source: str               # backend name
    project: str
    memory_type: str          # decision/event/discovery/preference/artifact
    similarity: float         # 0-1 vector similarity score
    metadata: dict            # backend-specific extras

@dataclass
class WakeUpContext:
    tokens: int               # estimated token count
    text: str                 # the context to inject
    layers: list[str]         # which layers contributed (L0, L1)
```

### Dispatcher

```python
# rawgentic_memory/dispatcher.py

class MemoryDispatcher:
    def __init__(self, config):
        self.native = NativeBackend(config)
        self.mempalace = MemPalaceBackend(config)
        self.active = config["activeBackend"]
        self.comparison_mode = config.get("comparisonMode", False)
        self.last_ingest_offset = {}  # per-project WAL offset

    def ingest(self, session_data):
        # Incremental: only process entries after last_ingest_offset
        new_entries = self._entries_since_offset(session_data)
        if not new_entries:
            return {"skipped": True}

        # Dual-write always
        native_result = self.native.ingest(new_entries)
        mempalace_result = self.mempalace.ingest(new_entries)
        self._update_offset(session_data.project, new_entries)
        return {"native": native_result, "mempalace": mempalace_result}

    def search(self, query, filters):
        if self.comparison_mode:
            native_results = self.native.search(query, filters)
            mempalace_results = self.mempalace.search(query, filters)
            self._log_comparison("search", query, native_results, mempalace_results)
            return native_results if self.active == "native" else mempalace_results
        return self._active_backend().search(query, filters)

    def wake_up(self, project):
        if self.comparison_mode:
            native_ctx = self.native.wake_up(project)
            mempalace_ctx = self.mempalace.wake_up(project)
            self._log_comparison("wakeup", project, native_ctx, mempalace_ctx)
            return native_ctx if self.active == "native" else mempalace_ctx
        return self._active_backend().wake_up(project)

    def _active_backend(self):
        return self.native if self.active == "native" else self.mempalace
```

### Config

```json
// ~/claude_docs/.config.json
{
  "claudeDocsPath": "~/claude_docs",
  "activeBackend": "native",
  "comparisonMode": true,
  "serverPort": 9077,
  "idleTimeoutHours": 4,
  "backends": {
    "native": { "enabled": true },
    "mempalace": { "enabled": true, "palacePath": "~/claude_docs/memory/mempalace" }
  },
  "ingestion": {
    "minToolCalls": 5,
    "requireFileEdits": false,
    "enrichOnIngest": true
  }
}
```

## Section 4: Three Ingestion Triggers

Sessions run for weeks/months with compaction. wal-stop fires as few as 3 times per 371 tool operations. The ingestion system must not depend on session stop.

### Trigger 1: PreCompact (primary)

Fires every time context compresses during a long-running session. This is the session "heartbeat."

```bash
# hooks/hooks.json addition:
{ "event": "PreCompact", "command": "hooks/memory-ingest" }
```

The hook collects WAL entries since `last_ingest_offset` and POSTs to `/ingest` with `"source": "precompact"`. Runs asynchronously -- must not block compaction.

### Trigger 2: Timer via UserPromptSubmit (safety net)

If a session runs for hours without compaction, ingest anyway. Piggybacks on the existing `wal-context` hook (UserPromptSubmit event).

```bash
# In wal-context: check elapsed time since last ingest
LAST_INGEST=$(cat ~/claude_docs/.last_ingest_ts 2>/dev/null || echo 0)
NOW=$(date +%s)
ELAPSED=$(( NOW - LAST_INGEST ))
if [ $ELAPSED -gt 7200 ]; then  # 2 hours
    memory_api_call "/ingest" "$INGEST_PAYLOAD" &
    disown
fi
```

### Trigger 3: Stop (final flush)

Catches anything accumulated since the last precompact/timer. Runs on the rare occasions a session actually ends.

```bash
# In wal-stop (after existing STOP marker logic):
memory_api_call "/ingest" "$INGEST_PAYLOAD"
```

### Incremental processing

All three triggers call the same `/ingest` endpoint. The server tracks `last_ingest_offset` per project to avoid reprocessing. Each ingest cycle only indexes new WAL entries and session note content since the last cycle.

## Section 5: Native Enhanced Backend

### Design principle

ChromaDB is a **search index** over existing archives, not a replacement. Source of truth remains in JSONL archives and markdown files. If ChromaDB corrupts, re-index from files and lose nothing.

### Storage

```
~/claude_docs/memory/native/
+-- collections.json          # ChromaDB collection metadata
+-- l0_identity.md            # who is this user, global prefs (~50 tokens)
+-- l1_critical.md            # auto-generated top facts per project (~120 tokens)
```

Raw data stays in `~/claude_docs/session_notes/`, `wal/`, and `session_notes/archive/`.

### ChromaDB metadata schema

```json
{
  "backend": "native",
  "project": "chorestory",
  "memory_type": "decision",
  "topic": "auth-migration",
  "source_file": "session_notes/archive/chorestory.jsonl",
  "source_entry": 42,
  "session_id": "abc-123",
  "timestamp": "2026-04-08T18:30:00Z"
}
```

### Memory type mapping

| Enrichment field | Memory type |
|---|---|
| `decisions` | `decision` |
| `sessions` with status changes | `event` |
| `patterns` | `discovery` |
| MEMORY.md `feedback` type files | `preference` |
| `artifacts` | `artifact` |

### Wake-up generation

- **L0:** Static `l0_identity.md` -- user identity, workspace overview. Written once during setup, updated manually. (~50 tokens)
- **L1:** Auto-regenerated `l1_critical.md` after each ingest. Top 10 most-referenced decisions/facts across all projects, ranked by recency + frequency. (~120 tokens)

### Ingest flow

1. Receive `SessionData` from dispatcher (incremental -- only new entries)
2. Run enrichment pipeline to extract decisions/patterns/artifacts
3. Index each enriched segment into ChromaDB with metadata
4. Regenerate `l1_critical.md` from top entries
5. Return `IngestResult` with counts

## Section 6: MemPalace Backend

### Concept mapping

| Rawgentic concept | MemPalace concept |
|---|---|
| Project name (chorestory, grocusave) | Wing |
| Enrichment category (decision, pattern, artifact) | Hall |
| Extracted topic (auth-migration, ci-pipeline) | Room |
| Session archive entry | Drawer (verbatim) |
| Cross-project shared topic | Tunnel (auto-detected) |

### Installation

```bash
pip install mempalace
```

Used as a Python library during comparison. If MemPalace wins, it gets installed properly as a Claude Code plugin and rawgentic becomes a consumer of its MCP tools.

### Ingest flow

1. Receive `SessionData` from dispatcher (incremental)
2. Map project to wing, memory_type to hall
3. Run MemPalace's room detection on content (or use enrichment topics)
4. Store each enriched segment as a drawer via `mempalace.miner` API
5. Add knowledge graph triples for entities/decisions from enrichment
6. Return `IngestResult` with counts

### Wake-up

- Uses MemPalace's `LayerStack` directly
- L0: identity from `~/.mempalace/identity.txt` (generated from rawgentic's user memory files)
- L1: auto-generated from top palace drawers (MemPalace's existing generator)

### Search

- Delegates to `mempalace.searcher.search_memories()` with wing/room filters
- Results normalized to shared `SearchResult` dataclass

### Knowledge graph

- Decision entities from enrichment added as temporal triples
- Example: `("chorestory", "decided", "use Valibot over Zod", valid_from="2026-03-15")`
- Stale decisions don't pollute search thanks to temporal validity windows

### What we skip

- AAAK dialect -- raw mode is better (96.6% vs 84.2%)
- Specialist agents -- rawgentic has its own workflow skills
- MCP tools -- not needed during comparison; available natively if MemPalace gets installed as plugin

## Section 7: Hook Integration (rawgentic repo changes)

### Changes to existing hooks

| Hook | Change |
|---|---|
| `session-start` Section 2b | Add HTTP wake-up call alongside existing archive context injection |
| `wal-stop` | Add HTTP ingest call after existing STOP marker logic |
| `wal-context` | Add timer-based ingest check (2h elapsed) |
| `wal-lib.sh` | Add `memory_api_call()` helper with lazy-start |
| NEW: `precompact` hook | HTTP ingest call (primary trigger) |

### Extraction strategy: additive, not destructive

Existing memory scripts (`archive-notes.py`, `query-archive.py`) stay in place and keep running. HTTP stubs are added alongside them. The old scripts are removed in a later cleanup phase after the memory server has been running reliably.

This means during the transition period, session-start injects context from BOTH the old archive query AND the new memory server wake-up (if running). No data loss, no behavior change if server is down.

### Example: session-start Section 2b after changes

```bash
# Existing archive context injection (unchanged):
QUERY_RESULT=$(python3 "$SCRIPT_DIR/query-archive.py" "$ARCHIVE_DIR" \
    --project "$PROJECT" --brief --limit 3 2>/dev/null) || true
if [ -n "$QUERY_RESULT" ]; then
    CONTEXT_PARTS+=("$QUERY_RESULT")
fi

# NEW: Memory server wake-up (additive, degrades gracefully):
MEMORY_WAKEUP=$(memory_api_call "GET" "/wakeup?project=$PROJECT")
if [ -n "$MEMORY_WAKEUP" ]; then
    CONTEXT_PARTS+=("$MEMORY_WAKEUP")
fi
```

## Section 8: Comparison Framework

### Two types of metrics (separated)

**Objective (ground truth benchmark):**
- Curated query/answer pairs mined from existing MEMORY.md entries and session history
- Automated R@1, R@5, project accuracy scoring
- No subjective bias -- measures actual retrieval correctness

**Subjective (user ratings):**
- One-keypress rating after each search: `(n)ative / (m)empalace / (s)ame`
- Periodic DX survey after every 10th session
- Measures perceived usefulness and experience

### Ground truth file

```jsonl
// ~/claude_docs/comparison/ground_truth.jsonl
{"id": "gt-001", "query": "what database did we choose for grocusave", "expected_project": "grocusave", "expected_type": "decision", "expected_answer_contains": ["postgres"]}
{"id": "gt-002", "query": "why did we switch from Zod to Valibot", "expected_project": "grocusave", "expected_type": "decision", "expected_answer_contains": ["SchemaWithPipe", "nesting"]}
{"id": "gt-003", "query": "what port does grocusave web run on", "expected_project": "grocusave", "expected_type": "artifact", "expected_answer_contains": ["3010"]}
{"id": "gt-020", "query": "how do we handle deployment to dev", "expected_projects": ["chorestory", "grocusave"], "cross_project": true}
```

Initial set mined from existing MEMORY.md entries (port numbers, architecture decisions, gotchas). Minimum 25-30 queries, at least 5-8 testing cross-project discovery.

### Benchmark endpoint

```python
# POST /benchmark
def run_benchmark(ground_truth_path):
    for gt in load_ground_truth(ground_truth_path):
        native_hits = native_backend.search(gt["query"])
        mempalace_hits = mempalace_backend.search(gt["query"])
        score_and_log(gt, native_hits, mempalace_hits)
    return aggregate_scores()  # R@1, R@5, project accuracy
```

### Live comparison logging

When `comparisonMode: true`, every search and wake-up queries both backends:

```jsonl
// ~/claude_docs/comparison/queries.jsonl
{
  "ts": "2026-04-15T10:00:00Z",
  "query": "why did we switch to Valibot",
  "project": "grocusave",
  "native": { "results": 5, "top_similarity": 0.87, "top_preview": "..." },
  "mempalace": { "results": 3, "top_similarity": 0.91, "top_preview": "..." },
  "user_rating": null
}
```

### Report format

```
=== Memory Backend Comparison (2026-04-08 -> 2026-04-22) ===

OBJECTIVE (ground truth, n=28):
  R@1:        native 68% | mempalace 75%
  R@5:        native 89% | mempalace 93%
  Project:    native 82% | mempalace 89%
  Cross-proj: native 2/8  | mempalace 5/8

SUBJECTIVE (user ratings, n=18):
  Preferred:  native 72% | mempalace 28% | same 0%

EFFICIENCY:
  Wake-up:    native avg 175 tok | mempalace avg 210 tok
  Ingest:     native avg 1.2s | mempalace avg 2.8s

VERDICT: [weighted score]
```

### Testing period

Minimum two weeks. Benchmark script can be run anytime via `/benchmark` endpoint.

## Section 9: Web Frontend and Visualization

### Setup

Two instances of [memory-palace-web-frontend](https://github.com/tomsalphaclawbot/memory-palace-web-frontend) pointing at each backend's ChromaDB:

- `localhost:8098` -- native backend browser
- `localhost:8099` -- MemPalace backend browser

Same UI, different data. Both backends use ChromaDB, so the same frontend works for both.

### Docker compose

```yaml
# ~/claude_docs/memory/frontend/docker-compose.yml
services:
  native-frontend:
    image: memory-palace-web-frontend
    ports: ["8098:8099"]
    volumes:
      - ../chromadb:/app/palace:ro
      - ./config/native.json:/app/config/palace.json:ro

  mempalace-frontend:
    image: memory-palace-web-frontend
    ports: ["8099:8099"]
    volumes:
      - ../mempalace:/app/palace:ro
      - ./config/mempalace.json:/app/config/palace.json:ro
```

### Management skill

`/rawgentic:memory-ui` with subcommands: `up`, `down`, `status`.

## Dependencies

### rawgentic repo (no new dependencies)

Only uses `curl` for HTTP calls (already available).

### rawgentic-memory repo

**Python:**
- `fastapi` + `uvicorn` -- API server
- `chromadb>=0.5.0,<0.7` -- vector search (both backends)
- `mempalace` -- MemPalace library (comparison period only)

**Docker (optional):**
- `memory-palace-web-frontend` image -- web visualization

## Rollout

### Phase 0: ~/claude_docs/ migration (rawgentic repo)

- Add `claudeDocsPath` to workspace config
- Update `wal-lib.sh` path resolution
- Migration script with symlink backward compat
- Update all hooks to use new paths
- Validate independently before proceeding

### Phase 1: Add HTTP stubs to rawgentic (rawgentic repo)

- Add `memory_api_call()` helper to `wal-lib.sh` (lazy-start, graceful degradation)
- Add HTTP wake-up call to session-start Section 2b (alongside existing archive context)
- Add precompact hook with HTTP ingest call
- Add timer check to wal-context (2h elapsed)
- Add HTTP ingest call to wal-stop
- Existing `archive-notes.py` and `query-archive.py` stay untouched
- All HTTP calls degrade silently if server unreachable
- **Stress tested during Phases 2-5 as the memory server is built**

### Phase 2: Create rawgentic-memory repo, build server + native backend

- `gh repo create 3D-Stories/rawgentic-memory`
- `/rawgentic:new-project` to register in workspace
- FastAPI server skeleton with lazy-start + idle timeout
- Native enhanced backend (ChromaDB over archives, L0/L1 tiered wake-up)
- Unit + integration tests

### Phase 3: Add MemPalace backend (rawgentic-memory repo)

- MemPalace wrapper behind abstraction layer
- Wing/room/hall mapping from rawgentic concepts
- Knowledge graph triple injection
- Dual-write dispatcher

### Phase 4: Comparison framework + ground truth (rawgentic-memory repo)

- Ground truth JSONL mined from existing MEMORY.md entries
- Benchmark endpoint with R@1, R@5, project accuracy
- Live comparison logging (queries, wakeups, ingestions)
- Report generation endpoint
- User rating collection in `/rawgentic:recall` skill

### Phase 5: Backfill + comparison period (rawgentic-memory repo)

- Index all existing session notes and WAL data into both backends
- Enable `comparisonMode`
- Run for minimum two weeks
- Web frontend setup for visual inspection

### Phase 6: Decide winner (both repos)

- Run final benchmark against ground truth
- Analyze comparison report
- If MemPalace wins: install as proper Claude Code plugin, update rawgentic to consume its MCP tools
- If native wins: drop mempalace dependency, keep enhanced native backend
- Remove comparison scaffolding

### Later: Clean up rawgentic (rawgentic repo)

- Remove `archive-notes.py`, `query-archive.py` from hooks/
- Remove session-start Section 2 (old archive logic)
- Remove enrichment dispatch block
- HTTP stubs become the sole memory integration path

## References

- [MemPalace](https://github.com/milla-jovovich/mempalace) -- upstream project, 96.6% LongMemEval R@5 in raw mode
- [Memory Palace Web Frontend](https://github.com/tomsalphaclawbot/memory-palace-web-frontend) -- read-only web UI for ChromaDB palace data
- MemPalace "Note from Milla & Ben" (April 7, 2026) -- honest corrections on AAAK, compression claims, and contradiction detection
