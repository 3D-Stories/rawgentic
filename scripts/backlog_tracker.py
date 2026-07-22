#!/usr/bin/env python3
"""Generate the rawgentic backlog tracker — a self-contained, fancy HTML dashboard of the
2026-07-22 benefit-ordered epic series (label `backlog-series-2507`) plus the Lane-0 hotfix and
the executor finishing tracks.

The CURATED structure below (epic order, grouping, benefit, effort) is the triage output; the
LIVE issue title / state / labels / body are pulled from `gh` at generate time. So updating the
tracker after any issue completes is just: re-run this script, then re-publish the Artifact
(same file path) and re-commit the HTML.

    python3 scripts/backlog_tracker.py            # writes docs/planning/2026-07-22-backlog-tracker.html
    python3 scripts/backlog_tracker.py --offline  # skip gh; render from a cached data sidecar

Self-contained (CSP-safe: no external fonts/scripts), theme-aware, DOM-builder rendering (no
innerHTML) so it passes the repo security hook and is safe to commit. Each issue card opens a
modal with the full issue body.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = "3D-Stories/rawgentic"
OUT = Path("docs/planning/2026-07-22-backlog-tracker.html")
DATA_CACHE = Path("docs/planning/2026-07-22-backlog-tracker.data.json")

# --- curated triage structure (2026-07-22; cross-model-endorsed) ---------------------------
# Each epic: key, title, hue, benefit, one-line "why", exit criterion, children [(num, benefit,
# effort, note)]. benefit ∈ {HIGH, MED-HIGH, MED, MED-LOW, LOW}; effort ∈ {S, M, L, S-M, M-L}.
STRUCTURE = {
    "generated_note": "Benefit-ordered backlog series — cleanup + restructure of all open "
                      "rawgentic issues, endorsed by a cross-model (gpt-5.6-sol) second opinion.",
    "lane0": {
        "title": "Lane 0 — immediate hotfix (standalone, not an epic child)",
        "hue": "#e5484d",
        "children": [(576, "HIGH", "S", "host-disk-fill test leak (~99%) — ship via /fix-bug now")],
    },
    "epics": [
        {"key": "E1", "num": 595, "title": "Autonomous-run safety & correctness",
         "hue": "#e5484d", "benefit": "HIGH",
         "why": "Workflow-engine bugs that break or endanger unattended runs.",
         "exit": "Unattended runs expose every gate as visible text, never false-STOP, dispatch "
                 "in the intended checkout, resume from every branch state, and handle "
                 "contaminated reviewer returns.",
         "children": [
             (379, "HIGH", "S", "circuit-breaker findings must be VISIBLE before AskUserQuestion (co-impl #370)"),
             (370, "MED", "S-M", "ambiguity breaker: exempt determinable findings + campaign resolve-and-log"),
             (371, "MED", "S-M", "worktree probe vs Agent-tool spawn dir — implementer dispatch dies"),
             (364, "MED", "S", "resume has no doc-only-commit branch state → resumes at Step 9 wrongly"),
             (365, "MED", "S-M", "contaminated reviewer returns (fabricated SHA/file) unhandled"),
             (362, "LOW", "S", "§7 per-step marker residual + drift guard"),
         ]},
        {"key": "E2", "num": 590, "title": "Telemetry substrate that actually lands",
         "hue": "#12a594", "benefit": "HIGH",
         "why": "The measurement substrate for every cost/timing lever + WF14/WF19 correctness.",
         "exit": "Records land automatically, carry stable per-run attribution, dedup by run_id, "
                 "preserve dispatch history, and reconcile against session-note evidence.",
         "children": [
             (588, "HIGH", "M", "run-record persistence is manual — dropped on compaction/driven runs (root)"),
             (589, "HIGH", "M", "#506 per-step timing never populates (0/28) — deps #588"),
             (363, "HIGH", "L", "usage_capture has no per-run attribution (7/9 runs null)"),
             (355, "MED", "S", "persist_record blind-appends → re-summarize duplicates a line"),
             (356, "MED", "M", "dispatches[] loses cross-session entries (dead-dispatch evidence)"),
             (361, "MED", "M", "Step-16 cross-checks record vs session-note truth (integrity capstone)"),
         ]},
        {"key": "E3", "num": 596, "title": "Concurrency & cross-project isolation",
         "hue": "#0091ff", "benefit": "MED-HIGH",
         "why": "Multi-session / cross-project runs silently corrupt each other today.",
         "exit": "Project/worktree/session identity prevents state, marker, report, and checkout "
                 "collisions; WF14 scores the correct run.",
         "children": [
             (593, "MED", "M", "unify session-notes location + worktree-key (Part A is a WF14 mis-score bug)"),
             (594, "MED", "M-L", "/switch auto-isolate a 2nd same-project session into a worktree"),
             (345, "MED", "M", ".wf2-state keyed by bare issue number collides across projects"),
             (346, "MED", "M", "WF14 latest reads rawgentic store → cross-project record unreachable"),
             (372, "MED", "S", "WF14 report path vs wal-bind-guard cross-project write deny"),
         ]},
        {"key": "E4", "num": 597, "title": "Unattended-run resilience & owner comms",
         "hue": "#8e4ec6", "benefit": "MED-HIGH",
         "why": "Keep overnight runs alive across session rotation; bound hooks; two-way owner channel.",
         "exit": "Resume survives session rotation; internal hooks have enforced deadlines; the "
                 "owner-comms substrate is split into shippable phases.",
         "children": [
             (586, "HIGH", "M", "durable resume launcher pins a session ID that breaks on /clear"),
             (380, "MED", "M", "wal-context needs an internal execution deadline"),
             (568, "MED", "L", "Hermes bidirectional comms — discovery/decomposition item (phases first)"),
         ]},
        {"key": "E5", "num": 360, "title": "Codex design & architecture workflows",
         "hue": "#f5a623", "benefit": "MED",
         "why": "Extend the Codex cross-model machinery to design (WF15) + architecture (WF16).",
         "exit": "gpt-5.6-sol pinned via config; /design and /architect workflows shipped on the "
                 "WF5/WF13 pattern.",
         "children": [
             (357, "MED", "S", "config-driven Codex model pin (gpt-5.6-sol) + CLI upgrade prereq"),
             (358, "MED", "M", "WF15 /rawgentic:design — Codex as primary design author"),
             (359, "MED", "M", "WF16 /rawgentic:architect — Codex system-level architecture"),
         ]},
        {"key": "E6", "num": 598, "title": "Workspace-skill hygiene & tooling",
         "hue": "#0d9488", "benefit": "MED-LOW",
         "why": "Quality-of-life skills + preflight/audit tooling that cut recurring friction.",
         "exit": "Superseded stopgaps gone; deploy-verify + skill-usage auditing workspace-wide; "
                 "toolchain preflight surfaces exact remediations.",
         "children": [
             (534, "MED", "S", "retire epic-run-analysis (now actionable — #508 shipped)"),
             (535, "MED", "S", "rev-diagram snapshot script — fullPage dual-theme gate"),
             (399, "LOW", "S", "admit-to-org-runners auto-infer target group"),
             (536, "MED", "M", "generalize studio-deploy → workspace deploy-verify skill"),
             (537, "MED", "M", "new-agent adoption security-vet skill"),
             (390, "MED", "M", "workspace-doctor toolchain preflight"),
             (391, "MED", "M", "session-index v2 (tool_events + count-sessions) → unblocks #400"),
             (400, "MED", "M", "WF17 skill-usage auditor (deps #391)"),
             (350, "MED", "M", "integrate Project CodeGuard security rules into WF2/WF3"),
         ]},
        {"key": "E7", "num": 599, "title": "Executor economics & routing",
         "hue": "#64748b", "benefit": "LOW",
         "why": "Token/cost optimization + routing ownability; revalidate premises after #475.",
         "exit": "Per-seat verbosity/cost tunable; bake-off sets project-ownable; ultracode-interop "
                 "resolved (built or closed) against post-#475 reality.",
         "children": [
             (549, "MED", "L", "per-seat response-token budgets (verbosity + aggregation + A/B)"),
             (484, "LOW", "M", "bake-off candidate sets as project config (intake-gate after #475)"),
             (450, "LOW", "L", "interoperate with ultracode / Workflow tool (deferred; intake-gate)"),
         ]},
    ],
    "finishing": {
        "title": "Finishing tracks — executor epics (outside the series; let them complete)",
        "hue": "#6b7280",
        "epics": [
            {"num": 475, "title": "orchestrator/executor wiring (W1–W12)",
             "children": [(474, "HIGH", "M", "W12 migration flip + legacy retirement — closes the epic")]},
            {"num": 560, "title": "executor hardening (H1–H6)",
             "children": [
                 (559, "HIGH", "L", "H6 end-to-end proving run — codex mutating cell + account-switch recovery"),
                 (570, "MED", "M", "work_product reconcile guard + collect crash-window (#559 8a follow-up)"),
                 (571, "MED", "M", "pre-PR hardening F5–F8 (recover/resume/collect binding)"),
             ]},
            {"num": 449, "title": "driver-bench W10 — deferred live run (#138)",
             "children": []},
        ],
    },
    "closed_this_pass": [
        (408, "epic: GLM backend follow-ups — all 4 children merged"),
        (457, "epic: executor spikes — all 5 children closed"),
        (578, "goal_guard enum — already shipped (deferred in enum since #191)"),
        (394, "reviewer vacuous-result protocol — core shipped via #329/#331"),
        (569, "fresh-session-per-child — shipped in PR #575"),
    ],
}


def _gh_issue(num: int) -> dict:
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(num), "--repo", REPO,
             "--json", "number,title,state,body,labels"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return {"number": num, "title": f"(issue #{num} — gh error)", "state": "UNKNOWN",
                    "body": out.stderr.strip() or "unavailable", "labels": []}
        d = json.loads(out.stdout)
        d["labels"] = [l["name"] for l in d.get("labels", [])]
        return d
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        return {"number": num, "title": f"(issue #{num} — {e})", "state": "UNKNOWN",
                "body": "unavailable", "labels": []}


def collect(offline: bool) -> dict:
    if offline and DATA_CACHE.exists():
        return json.loads(DATA_CACHE.read_text())
    nums = set()
    for c in STRUCTURE["lane0"]["children"]:
        nums.add(c[0])
    for e in STRUCTURE["epics"]:
        nums.add(e["num"])
        for c in e["children"]:
            nums.add(c[0])
    for e in STRUCTURE["finishing"]["epics"]:
        nums.add(e["num"])
        for c in e["children"]:
            nums.add(c[0])
    for n, _ in STRUCTURE["closed_this_pass"]:
        nums.add(n)
    issues = {}
    for n in sorted(nums):
        print(f"  fetching #{n} ...", file=sys.stderr)
        issues[str(n)] = _gh_issue(n)
    data = {"issues": issues,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    DATA_CACHE.write_text(json.dumps(data, indent=1))
    return data


def build_html(data: dict) -> str:
    payload = json.dumps({"structure": STRUCTURE, "data": data},
                         ensure_ascii=False, separators=(",", ":"))
    # Escape `<` so an issue body containing `</script>` can't break out of the
    # embedded <script> block (< decodes back to `<` in the JS object literal).
    payload = payload.replace("<", "\\u003c")
    # The page: static shell + one embedded JSON blob + DOM-builder renderer (no innerHTML).
    return _TEMPLATE.replace("/*__PAYLOAD__*/", payload)


# --- the HTML template (CSS + DOM-builder JS). __PAYLOAD__ is replaced with the JSON blob. ----
_TEMPLATE = r"""<meta charset="utf-8">
<title>rawgentic backlog tracker</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#ffffff; --panel-2:#eef1f5; --ink:#11161d; --ink-2:#4a5568;
  --line:#d9dee6; --line-2:#c4ccd6; --accent:#0091ff;
  --ok:#13a05e; --warn:#c98a00; --crit:#e5484d; --muted:#8b95a3;
  --s-open:#64748b; --s-prog:#c98a00; --s-done:#13a05e; --s-closed:#94a3b8;
  --b-high:#e5484d; --b-medhigh:#e6822a; --b-med:#c98a00; --b-medlow:#0d9488; --b-low:#64748b;
  --shadow:0 1px 2px rgba(16,22,29,.06),0 8px 24px rgba(16,22,29,.06);
  --radius:14px; --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0f1216; --panel:#161b22; --panel-2:#1c232c; --ink:#e6edf3; --ink-2:#9aa7b4;
  --line:#262d36; --line-2:#333c47; --accent:#4aa8ff; --muted:#768390;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35);
}}
:root[data-theme="dark"]{
  --bg:#0f1216; --panel:#161b22; --panel-2:#1c232c; --ink:#e6edf3; --ink-2:#9aa7b4;
  --line:#262d36; --line-2:#333c47; --accent:#4aa8ff;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35);
}
:root[data-theme="light"]{
  --bg:#f6f7f9; --panel:#ffffff; --panel-2:#eef1f5; --ink:#11161d; --ink-2:#4a5568;
  --line:#d9dee6; --line-2:#c4ccd6; --accent:#0091ff;
  --shadow:0 1px 2px rgba(16,22,29,.06),0 8px 24px rgba(16,22,29,.06);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 80px}
.tnum{font-family:var(--mono);font-variant-numeric:tabular-nums}
h1{font-size:26px;letter-spacing:-.02em;margin:0 0 4px;text-wrap:balance}
.sub{color:var(--ink-2);font-size:14px;margin:0}
.head{display:flex;flex-wrap:wrap;gap:20px;align-items:flex-end;justify-content:space-between;
  border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:24px}
.meta{color:var(--muted);font-size:12px;font-family:var(--mono)}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 26px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;
  min-width:120px;box-shadow:var(--shadow)}
.stat .n{font-size:22px;font-weight:700;font-family:var(--mono);font-variant-numeric:tabular-nums}
.stat .l{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.bar{height:8px;border-radius:99px;background:var(--panel-2);overflow:hidden;margin-top:8px}
.bar > i{display:block;height:100%;background:var(--s-done);border-radius:99px}
.hotfix{border:1px solid var(--crit);border-left:6px solid var(--crit);border-radius:12px;
  background:color-mix(in srgb,var(--crit) 8%,var(--panel));padding:14px 18px;margin:0 0 26px}
.hotfix .tag{color:var(--crit);font-weight:700;font-size:12px;letter-spacing:.08em;
  text-transform:uppercase}
.epic{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow);margin:0 0 22px;overflow:hidden}
.epic > .top{border-top:5px solid var(--hue);padding:16px 18px 14px}
.epic .row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.epic h2{font-size:18px;margin:0;letter-spacing:-.01em}
.rank{font-family:var(--mono);font-weight:700;font-size:12px;color:#fff;background:var(--hue);
  padding:3px 8px;border-radius:7px;letter-spacing:.02em}
.epic .why{color:var(--ink-2);font-size:13.5px;margin:8px 0 0}
.epic .exit{color:var(--muted);font-size:12.5px;margin:8px 0 0;border-left:2px solid var(--line-2);
  padding-left:10px}
.epic .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;
  padding:4px 18px 18px}
.card{border:1px solid var(--line);border-radius:11px;background:var(--panel);padding:11px 12px;
  cursor:pointer;transition:transform .08s ease,border-color .12s ease,box-shadow .12s ease;
  display:flex;flex-direction:column;gap:7px;text-align:left;font:inherit;color:inherit;width:100%}
.card:hover{transform:translateY(-2px);border-color:var(--hue);box-shadow:var(--shadow)}
.card:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.card .id{display:flex;align-items:center;gap:8px}
.card .id b{font-family:var(--mono);font-size:13px}
.card .ti{font-size:13px;line-height:1.35;color:var(--ink)}
.card .note{font-size:12px;color:var(--ink-2);line-height:1.4}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}
.chip{font-size:10.5px;font-weight:600;letter-spacing:.03em;padding:2px 7px;border-radius:99px;
  text-transform:uppercase}
.pill{font-size:10.5px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:99px;
  text-transform:uppercase;color:#fff}
.eff{background:var(--panel-2);color:var(--ink-2);border:1px solid var(--line)}
.done .ti,.closed .ti{text-decoration:line-through;opacity:.6}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:8px 0 24px;font-size:12px;color:var(--ink-2)}
.legend b{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;
  vertical-align:middle}
.fin{opacity:.92}
.fin .epic .top{border-top-color:var(--muted)}
.foot{color:var(--muted);font-size:12px;margin-top:30px;border-top:1px solid var(--line);
  padding-top:16px}
.toggle{background:var(--panel);border:1px solid var(--line);border-radius:9px;color:var(--ink-2);
  font:inherit;font-size:12px;padding:6px 11px;cursor:pointer}
/* modal */
.ov{position:fixed;inset:0;background:rgba(6,9,13,.55);backdrop-filter:blur(3px);display:none;
  align-items:flex-start;justify-content:center;padding:40px 16px;z-index:50;overflow:auto}
.ov.on{display:flex}
.modal{background:var(--panel);border:1px solid var(--line-2);border-radius:16px;max-width:820px;
  width:100%;box-shadow:0 24px 70px rgba(0,0,0,.45);overflow:hidden}
.modal .mtop{padding:18px 22px;border-bottom:1px solid var(--line);border-top:5px solid var(--hue)}
.modal h3{margin:6px 0 0;font-size:18px;letter-spacing:-.01em;text-wrap:balance}
.modal .mmeta{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-top:10px}
.modal .body{padding:18px 22px 26px;max-height:60vh;overflow:auto}
.modal .body pre{font-family:var(--mono);font-size:12.5px;line-height:1.55;white-space:pre-wrap;
  word-wrap:break-word;margin:0;color:var(--ink)}
.mlabels{font-family:var(--mono);font-size:11px;color:var(--muted)}
.x{margin-left:auto;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;
  width:30px;height:30px;cursor:pointer;color:var(--ink);font-size:16px;line-height:1}
.linkout{font-size:12px;color:var(--accent);text-decoration:none;font-family:var(--mono)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <div class="head">
    <div>
      <h1>rawgentic backlog tracker</h1>
      <p class="sub" id="sub"></p>
    </div>
    <div style="display:flex;gap:12px;align-items:center">
      <div class="meta" id="gen"></div>
      <button class="toggle" id="themeBtn" type="button">◐ theme</button>
    </div>
  </div>
  <div class="summary" id="summary"></div>
  <div class="legend" id="legend"></div>
  <div id="root"></div>
  <div class="foot" id="foot"></div>
</div>

<div class="ov" id="ov"><div class="modal" id="modal"></div></div>

<script>
const PAYLOAD=/*__PAYLOAD__*/;
const S=PAYLOAD.structure, ISS=PAYLOAD.data.issues;
const el=(t,c,txt)=>{const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e;};
const BEN={"HIGH":"--b-high","MED-HIGH":"--b-medhigh","MED":"--b-med","MED-LOW":"--b-medlow","LOW":"--b-low"};
function status(num){
  const it=ISS[String(num)]||{};
  const st=(it.state||"OPEN").toUpperCase();
  if(st==="CLOSED"){
    // closed child that is one of our deliverables counts as done; closed cleanup = closed
    return "done";
  }
  return "open";
}
function benefitChip(b){const c=el("span","pill",b);c.style.background=`var(${BEN[b]||"--b-low"})`;return c;}
function statusPill(kind){
  const map={open:["OPEN","--s-open"],done:["DONE","--s-done"],prog:["IN PROGRESS","--s-prog"],closed:["CLOSED","--s-closed"]};
  const [t,v]=map[kind]||map.open;const p=el("span","pill",t);p.style.background=`var(${v})`;return p;
}
function ghLink(num){
  const a=el("a","linkout gh","#"+num+" ↗");
  a.href="https://github.com/3D-Stories/rawgentic/issues/"+num;
  a.target="_blank";a.rel="noreferrer";a.title="open issue #"+num+" on GitHub";
  a.addEventListener("click",e=>e.stopPropagation());  // don't trigger the modal
  return a;
}
function card(child,hue){
  const [num,ben,eff,note]=child;const it=ISS[String(num)]||{};
  const kind=status(num);
  const b=el("div","card "+(kind==="done"?"done":""));b.style.setProperty("--hue",hue);
  b.setAttribute("role","button");b.setAttribute("tabindex","0");
  b.setAttribute("aria-haspopup","dialog");
  const id=el("div","id");const nb=el("b",null,"#"+num);id.appendChild(nb);
  id.appendChild(statusPill(kind));
  const sp=el("span");sp.style.marginLeft="auto";id.appendChild(sp);
  id.appendChild(ghLink(num));           // per-issue GitHub link on every card
  b.appendChild(id);
  b.appendChild(el("div","ti",it.title||("issue #"+num)));
  if(note)b.appendChild(el("div","note",note));
  const ch=el("div","chips");ch.appendChild(benefitChip(ben));
  const e=el("span","chip eff","effort "+eff);ch.appendChild(e);b.appendChild(ch);
  const open=()=>openModal(num,hue,ben,eff);
  b.addEventListener("click",open);
  b.addEventListener("keydown",ev=>{if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();open();}});
  return b;
}
function epicBox(e){
  const box=el("div","epic");const top=el("div","top");top.style.setProperty("--hue",e.hue);
  const row=el("div","row");
  const rk=el("span","rank",e.key+" · "+e.benefit);rk.style.setProperty("--hue",e.hue);
  row.appendChild(rk);
  const h=el("h2",null,e.title);row.appendChild(h);
  const it=ISS[String(e.num)]||{};
  const lo=el("a","linkout","#"+e.num+" ↗");lo.href="https://github.com/3D-Stories/rawgentic/issues/"+e.num;
  lo.target="_blank";lo.rel="noreferrer";row.appendChild(lo);
  top.appendChild(row);
  const total=e.children.length;
  const done=e.children.filter(c=>status(c[0])==="done").length;
  const barWrap=el("div","bar");const fill=el("i");fill.style.width=(total?Math.round(done/total*100):0)+"%";
  fill.style.background=e.hue;barWrap.appendChild(fill);top.appendChild(barWrap);
  const prog=el("div","meta",done+" / "+total+" issues complete");prog.style.marginTop="6px";top.appendChild(prog);
  top.appendChild(el("p","why",e.why));
  if(e.exit)top.appendChild(el("p","exit","Exit: "+e.exit));
  box.appendChild(top);
  const cards=el("div","cards");
  e.children.forEach(c=>cards.appendChild(card(c,e.hue)));
  box.appendChild(cards);
  // epic title itself opens the epic modal
  h.style.cursor="pointer";h.addEventListener("click",()=>openModal(e.num,e.hue,e.benefit,"epic"));
  return box;
}
function openModal(num,hue,ben,eff){
  const it=ISS[String(num)]||{};const m=document.getElementById("modal");
  while(m.firstChild)m.removeChild(m.firstChild);
  m.style.setProperty("--hue",hue||"#0091ff");
  const top=el("div","mtop");
  const meta=el("div","mmeta");
  const nb=el("span","tnum");nb.style.fontWeight="700";nb.textContent="#"+num;meta.appendChild(nb);
  meta.appendChild(statusPill(status(num)));
  if(ben&&eff!=="epic"){meta.appendChild(benefitChip(ben));meta.appendChild(el("span","chip eff","effort "+eff));}
  const lo=el("a","linkout","open on GitHub ↗");
  lo.href="https://github.com/3D-Stories/rawgentic/issues/"+num;lo.target="_blank";lo.rel="noreferrer";
  const x=el("button","x","×");x.setAttribute("aria-label","close");x.addEventListener("click",closeModal);
  meta.appendChild(lo);meta.appendChild(x);
  top.appendChild(meta);
  top.appendChild(el("h3",null,it.title||("issue #"+num)));
  if(it.labels&&it.labels.length)top.appendChild(el("div","mlabels",it.labels.join(" · ")));
  m.appendChild(top);
  const body=el("div","body");const pre=el("pre",null,(it.body||"(no description)").trim());
  body.appendChild(pre);m.appendChild(body);
  document.getElementById("ov").classList.add("on");
  x.focus();
}
function closeModal(){document.getElementById("ov").classList.remove("on");}
document.getElementById("ov").addEventListener("click",e=>{if(e.target.id==="ov")closeModal();});
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeModal();});

// theme toggle
const rootEl=document.documentElement;
document.getElementById("themeBtn").addEventListener("click",()=>{
  const cur=rootEl.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
  rootEl.setAttribute("data-theme",cur==="dark"?"light":"dark");
});

// render
document.getElementById("sub").textContent=S.generated_note;
document.getElementById("gen").textContent="generated "+PAYLOAD.data.generated_at;
const root=document.getElementById("root");
// summary
let allChildren=[];S.epics.forEach(e=>allChildren=allChildren.concat(e.children.map(c=>c[0])));
S.lane0.children.forEach(c=>allChildren.push(c[0]));
const doneCount=allChildren.filter(n=>status(n)==="done").length;
const summary=document.getElementById("summary");
[["epics",S.epics.length,"benefit-ordered"],
 ["open issues",allChildren.filter(n=>status(n)!=="done").length,"across the series"],
 ["done",doneCount,"completed"],
 ["closed this pass",S.closed_this_pass.length,"cleanup"]].forEach(([l,n,s2])=>{
  const c=el("div","stat");const nn=el("div","n",String(n));c.appendChild(nn);
  c.appendChild(el("div","l",l));
  const bar=el("div","bar");const f=el("i");
  f.style.width=(l==="done"&&allChildren.length?Math.round(doneCount/allChildren.length*100):(l==="done"?0:0))+"%";
  if(l==="done"){bar.appendChild(f);c.appendChild(bar);}
  summary.appendChild(c);
});
// legend
const legend=document.getElementById("legend");
[["High","--b-high"],["Med-High","--b-medhigh"],["Med","--b-med"],["Med-Low","--b-medlow"],["Low","--b-low"],
 ["Done","--s-done"],["Open","--s-open"]].forEach(([t,v])=>{
  const s=el("span",null);const sw=el("b");sw.style.background=`var(${v})`;s.appendChild(sw);
  s.appendChild(document.createTextNode(t));legend.appendChild(s);
});
// lane 0
const l0=S.lane0;const hf=el("div","hotfix");hf.appendChild(el("div","tag",l0.title));
const hfcards=el("div","cards");hfcards.style.padding="10px 0 0";
l0.children.forEach(c=>hfcards.appendChild(card(c,l0.hue)));hf.appendChild(hfcards);root.appendChild(hf);
// epics
S.epics.forEach(e=>root.appendChild(epicBox(e)));
// finishing tracks
const fin=el("div","fin");fin.appendChild(el("h2",null,S.finishing.title)).style.cssText="font-size:15px;margin:26px 0 12px;color:var(--muted)";
S.finishing.epics.forEach(fe=>{
  const box=el("div","epic");const top=el("div","top");top.style.setProperty("--hue",S.finishing.hue);
  const row=el("div","row");row.appendChild(el("h2",null,fe.title));
  const lo=el("a","linkout","#"+fe.num+" ↗");lo.href="https://github.com/3D-Stories/rawgentic/issues/"+fe.num;
  lo.target="_blank";lo.rel="noreferrer";row.appendChild(lo);top.appendChild(row);box.appendChild(top);
  if(fe.children.length){const cards=el("div","cards");fe.children.forEach(c=>cards.appendChild(card(c,S.finishing.hue)));box.appendChild(cards);}
  fin.appendChild(box);
});
root.appendChild(fin);
// closed
const cl=el("div");cl.appendChild(el("h2",null,"Closed this cleanup pass")).style.cssText="font-size:15px;margin:26px 0 10px;color:var(--muted)";
const clcards=el("div","cards");clcards.style.gridTemplateColumns="repeat(auto-fill,minmax(320px,1fr))";
S.closed_this_pass.forEach(([num,desc])=>{
  const b=el("div","card closed");b.style.setProperty("--hue","#94a3b8");
  b.setAttribute("role","button");b.setAttribute("tabindex","0");
  const id=el("div","id");const nb=el("b",null,"#"+num);id.appendChild(nb);id.appendChild(statusPill("closed"));
  const sp=el("span");sp.style.marginLeft="auto";id.appendChild(sp);id.appendChild(ghLink(num));b.appendChild(id);
  b.appendChild(el("div","note",desc));
  const op=()=>openModal(num,"#94a3b8","","closed");
  b.addEventListener("click",op);
  b.addEventListener("keydown",ev=>{if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();op();}});
  clcards.appendChild(b);
});
cl.appendChild(clcards);root.appendChild(cl);
// foot
document.getElementById("foot").textContent=
  "Living tracker — regenerate after any issue completes: `python3 scripts/backlog_tracker.py`, then re-publish the Artifact (same file path) and re-commit. Statuses + titles + full issue bodies are pulled live from GitHub at generate time; click any card for the issue's full description.";
</script>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="render from the cached data sidecar")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    data = collect(args.offline)
    html = build_html(data)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(html, encoding="utf-8")
    done = sum(1 for e in STRUCTURE["epics"] for c in e["children"]
               if data["issues"].get(str(c[0]), {}).get("state", "OPEN").upper() == "CLOSED")
    print(f"wrote {args.out} ({len(html)} bytes); {done} series children already closed",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
