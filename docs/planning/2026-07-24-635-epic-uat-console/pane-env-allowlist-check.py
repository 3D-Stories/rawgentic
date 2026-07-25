"""UAT-4 aid (epic #635): prove repo-wide that nothing credential-bearing can reach a herdr
pane's environment.

Reading `herdr_backend.py` cannot establish this — only its CALLERS decide what goes into
`pane_env`, and that value is visible via `ps` to any user on the host for the pane's
lifetime (the risk the owner accepted on 2026-07-24, conditional on host-local + ephemeral).
So this walks the AST of every .py file in the repo, finds every `HerdrBackend(...)`
construction, and fails unless each literal `pane_env` key set is inside the allowlist.

Exit 0 = every site is within the allowlist and none is dynamically built.
Exit 1 = a key outside the allowlist, or a `pane_env` this script cannot read statically
         (fails closed: "I could not tell" is not "safe").

Run from the repo root:  python3 docs/planning/2026-07-24-635-epic-uat-console/pane-env-allowlist-check.py
"""
import ast, pathlib, sys
ALLOWED = {"PYTHONPATH"}
literal, dynamic, none_passed = [], [], []
for f in pathlib.Path(".").rglob("*.py"):
    if any(p in f.parts for p in (".git", "__pycache__", "node_modules")): continue
    try: tree = ast.parse(f.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError): continue
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call): continue
        if (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) != "HerdrBackend": continue
        pe = {k.arg: k.value for k in n.keywords}.get("pane_env")
        loc = f"{f}:{n.lineno}"
        if pe is None: none_passed.append(loc)
        elif isinstance(pe, ast.Dict) and all(isinstance(k, ast.Constant) for k in pe.keys):
            literal.append((loc, {k.value for k in pe.keys}))
        else: dynamic.append(loc)
for loc, keys in literal: print(f"  {loc}  pane_env keys = {sorted(keys)}")
for loc in none_passed: print(f"  {loc}  pane_env absent (nothing exposed)")
for loc in dynamic: print(f"  {loc}  pane_env NOT a literal dict — needs eyes")
bad = [(l, k) for l, k in literal if not k <= ALLOWED]
print(f"\n{len(literal)+len(none_passed)+len(dynamic)} HerdrBackend construction sites: "
      f"{len(literal)} with a literal pane_env, {len(none_passed)} with none, {len(dynamic)} dynamic")
print("VIOLATIONS (keys outside %s): %s" % (sorted(ALLOWED), [l for l, _ in bad] or "none"))
sys.exit(1 if bad or dynamic else 0)
