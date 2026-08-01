Render through the **`design-doc-publish` add-on**, by ABSOLUTE path. A rawgentic skill runs
with the shell's cwd set to whatever project the session is bound to, so a repo-relative path
resolves only by accident — that was the bug (#807). The add-on is **suggested, not required**.

Substitute `<MD>`, `<OUT>`, `<TITLE>`, `<STYLE>` and `<TELEMETRY>` as **single-quoted shell
tokens**, rewriting every embedded `'` as `'\''`. Pass `''` for `<TELEMETRY>` when the caller has
no run-record to embed. Substitute nothing else — the heredoc is quoted, so the shell expands
nothing inside the program and the five values reach it as `argv`, never as shell words.

```bash
python3 - '<MD>' '<OUT>' '<TITLE>' '<STYLE>' '<TELEMETRY>' <<'RAWGENTIC_RENDER'
import errno, html, os, subprocess, sys, tempfile

md, out, title, style, telemetry = sys.argv[1:6]


def die(code, msg):
    print(msg, file=sys.stderr)
    sys.exit(code)


def stat_or(path, follow=True):
    """(st, errno) — never raises. errno is None on success."""
    try:
        return (os.stat(path) if follow else os.lstat(path)), None
    except OSError as e:
        return None, e.errno


# 1-2. Resolve the configuration root. An override must be absolute; a relative or
# empty one is a configuration error, never a silent fall-through to $HOME.
ccd = os.environ.get("CLAUDE_CONFIG_DIR")
explicit = bool(ccd)
if explicit:
    if not os.path.isabs(ccd):
        die(71, "config-error: CLAUDE_CONFIG_DIR is not an absolute path")
    root = ccd
else:
    home = os.environ.get("HOME")
    if not home or not os.path.isabs(home):
        die(71, "config-error: HOME is unset, empty, or not an absolute path")
    root = os.path.join(home, ".claude")

# 3-4. The root. A MISSING root splits on who chose it: an explicit CLAUDE_CONFIG_DIR
# pointing at nothing is a configuration error (the user named a place that is not
# there), but a default ~/.claude that does not exist just means there is no Claude
# configuration at all — and therefore certainly no add-on, which is ABSENT, not an
# error. Any OTHER errno is config-error either way: we cannot tell, and "cannot tell"
# must never be reported as "cleanly absent".
st, err = stat_or(root)
if err == errno.ENOENT and explicit:
    die(71, "config-error: CLAUDE_CONFIG_DIR points at %s, which does not exist" % root)
if err is not None and err != errno.ENOENT:
    die(71, "config-error: cannot read %s (errno %d)" % (root, err))
if err is None and not os.path.isdir(root):
    die(71, "config-error: %s is not a directory" % root)
root_missing = err == errno.ENOENT

addon = os.path.join(root, "skills", "design-doc-publish")
launcher = os.path.join(addon, "scripts", "render-doc")

# 5-6. Absent is reachable ONLY by ENOENT through a root already proven readable.
# A dangling symlink, EACCES, ELOOP or anything else is broken, never absent.
lst, err = stat_or(addon, follow=False) if not root_missing else (None, errno.ENOENT)
if err == errno.ENOENT:
    body = open(md, encoding="utf-8").read() if os.path.exists(md) else ""
    page = (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>%s</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:46rem;"
        "margin:3rem auto;padding:0 1rem}pre{white-space:pre-wrap;word-wrap:break-word}"
        ".b{background:#fdf2d0;border-left:4px solid #c90;padding:.75rem 1rem;margin-bottom:2rem}"
        "</style></head><body>\n<div class=\"b\">Rendered without the "
        "<strong>design-doc-publish</strong> add-on, which is not installed. This is a plain "
        "fallback page: the source is reproduced verbatim below, unstyled.</div>\n"
        "<h1>%s</h1>\n<pre>%s</pre>\n</body></html>\n"
    ) % (html.escape(title), html.escape(title), html.escape(body))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    die(20, "design-doc-publish is not installed; wrote a plain fallback page to %s" % out)
if err is not None:
    die(70, "broken: cannot read %s (errno %d)" % (addon, err))
if os.path.islink(addon):
    # The supported installation IS a symlink, so follow it — but a DANGLING one is
    # a half-installed add-on, which must fail loudly rather than look absent.
    _, err = stat_or(addon)
    if err is not None:
        die(70, "broken: %s is a dangling symlink (errno %d)" % (addon, err))
if not os.path.isdir(addon):
    die(70, "broken: %s exists but is not a directory" % addon)

st, err = stat_or(launcher)
if err is not None:
    die(70, "broken: add-on present but %s is unreadable (errno %d)" % (launcher, err))
if not os.path.isfile(launcher) or not os.access(launcher, os.X_OK):
    die(70, "broken: %s is not an executable regular file" % launcher)

# 7-8. Render to a temp file beside the destination; promote only after it validates,
# so a failed render can never clobber a good artifact.
dest_dir = os.path.dirname(os.path.abspath(out)) or "."
fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".html.tmp")
os.close(fd)
try:
    argv = [launcher, "--md", md, "--out", tmp, "--title", title, "--style", style]
    if telemetry:
        argv += ["--telemetry", telemetry]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        die(70, "broken: renderer exited %d\n%s" % (proc.returncode, proc.stderr.strip()))
    produced = open(tmp, encoding="utf-8").read()
    if not produced.strip():
        die(70, "broken: renderer produced an empty file")
    if not produced.lstrip().lower().startswith("<!doctype"):
        die(70, "broken: renderer output is not an HTML document")
    if html.escape(title) not in produced:
        die(70, "broken: renderer output does not contain the requested title")
    os.replace(tmp, out)
    tmp = None
finally:
    if tmp and os.path.exists(tmp):
        os.unlink(tmp)
RAWGENTIC_RENDER
```

**Exit codes — branch on these, never on the message text.** `0` rendered · `20` add-on absent
and a plain fallback page was written (say so out loud in your output) · `70` the add-on is
present but broken · `71` the configuration directory could not be determined. `70` and `71`
are failures: never treat either as the fallback.
