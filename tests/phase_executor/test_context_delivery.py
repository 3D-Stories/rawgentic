"""#829 — `--context-file` must actually REACH the provider, not just the audit trail.

Before this, `engine.py` stored `context` on the `AdapterRequest` and every adapter sent
`req.prompt` alone; `capture.hash_context` was the only consumer, so a dispatch that attached an
artifact produced non-empty `context_hashes` — positive audit evidence — while the model received
only the brief. That is worse than dropping the flag silently: the trail asserts a delivery that
never happened, which is exactly the class epic #756 exists to kill.

These tests pin the two halves that were missing:
- the composition itself is pure and deterministic (delimited, ordered, prompt first);
- each adapter's PROVIDER PAYLOAD and its on-disk capture both CONTAIN the context bytes. A stub
  returning success is not evidence — every adapter test asserts on what was actually handed to
  the transport.
"""
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

from phase_executor.adapters import base  # noqa: E402
from phase_executor.adapters.base import AdapterRequest, compose_provider_input  # noqa: E402


_PROMPT = "# Review brief\n\nReview the attached design."
_CTX_A = "# Design doc\n\nThe design says ALPHA.\n"
_CTX_B = "diff --git a/x b/x\n+BETA\n"


# --------------------------------------------------------------- the pure composition
def test_compose_no_context_is_the_prompt_unchanged():
    # byte-identical when nothing is attached — the 107 self-contained dispatches must not move
    assert compose_provider_input(_PROMPT, ()) == _PROMPT


def test_compose_includes_every_context_item_verbatim():
    out = compose_provider_input(_PROMPT, (_CTX_A, _CTX_B))
    assert _CTX_A in out
    assert _CTX_B in out


def test_compose_puts_prompt_first_and_preserves_order():
    out = compose_provider_input(_PROMPT, (_CTX_A, _CTX_B))
    assert out.startswith(_PROMPT)
    assert out.index(_CTX_A) < out.index(_CTX_B)


def test_compose_delimits_items_so_the_model_can_tell_them_apart():
    out = compose_provider_input(_PROMPT, (_CTX_A, _CTX_B))
    # a bare concatenation would let an artifact's text read as further instructions
    assert out.count("BEGIN ATTACHED CONTEXT") == 2
    assert out.count("END ATTACHED CONTEXT") == 2
    assert "1 of 2" in out and "2 of 2" in out


def test_compose_is_deterministic():
    assert compose_provider_input(_PROMPT, (_CTX_A,)) == compose_provider_input(_PROMPT, (_CTX_A,))


def test_compose_tolerates_none_context():
    assert compose_provider_input(_PROMPT, None) == _PROMPT


# ------------------------------------------------------- adapter boundary: what is SENT
def _req(**over):
    base_kw = dict(seat="review", requested_model="claude-sonnet-5", prompt=_PROMPT,
                   transport="native", context=(_CTX_A,), correlation_id="c1",
                   effort=None, timeout=5.0)
    base_kw.update(over)
    return AdapterRequest(**base_kw)


def _capture_stdin(monkeypatch, module):
    """Intercept the adapter's transport and record the exact stdin it hands over."""
    seen = {}

    def fake_run(cmd, stdin, timeout, **kw):
        seen["cmd"] = list(cmd)
        seen["stdin"] = stdin
        return base.ProcOutcome(stdout='{"ok":true}', stderr="", returncode=0, timed_out=False)

    monkeypatch.setattr(module, "run_subprocess", fake_run)
    return seen


@pytest.mark.parametrize("modname", ["codex_cli", "claude_cli"])
def test_subprocess_adapter_sends_the_context_bytes(modname, monkeypatch, tmp_path):
    mod = __import__(f"phase_executor.adapters.{modname}", fromlist=[modname])
    seen = _capture_stdin(monkeypatch, mod)
    try:
        mod.run(_req(), run_id="r1", attempt_id="0-a", capture_root=str(tmp_path),
                routing_config_digest="sha256:d", queued_ms=0, fallback_reason=None)
    except Exception as exc:  # adapters do more than transport; only the payload is under test
        if "stdin" not in seen:
            pytest.fail(f"{modname} never reached the transport: {type(exc).__name__}: {exc}")
    assert "stdin" in seen, f"{modname} never called run_subprocess"
    assert _CTX_A in seen["stdin"], (
        f"{modname} sent the prompt without the attached context — the model would review nothing")
    assert seen["stdin"].startswith(_PROMPT)


@pytest.mark.parametrize("modname", ["codex_cli", "claude_cli"])
def test_subprocess_adapter_capture_records_what_was_sent(modname, monkeypatch, tmp_path):
    """The on-disk `input.md` is the forensic surface — it is what a human reads months later to
    decide whether a review actually saw its artifact. It must equal the delivered bytes."""
    mod = __import__(f"phase_executor.adapters.{modname}", fromlist=[modname])
    seen = _capture_stdin(monkeypatch, mod)
    try:
        mod.run(_req(), run_id="r1", attempt_id="0-a", capture_root=str(tmp_path),
                routing_config_digest="sha256:d", queued_ms=0, fallback_reason=None)
    except Exception:
        pass
    inputs = list(Path(tmp_path).rglob("input.md"))
    assert inputs, f"{modname} wrote no capture input"
    written = inputs[0].read_text(encoding="utf-8")
    assert _CTX_A in written, f"{modname} capture omits the context that was sent"
    assert written == seen.get("stdin"), "capture must be byte-identical to what was delivered"


def test_no_context_leaves_the_payload_byte_identical(monkeypatch, tmp_path):
    """Regression pin: a contextless dispatch must send exactly the prompt, as before #829."""
    from phase_executor.adapters import codex_cli
    seen = _capture_stdin(monkeypatch, codex_cli)
    try:
        codex_cli.run(_req(context=()), run_id="r1", attempt_id="0-a",
                      capture_root=str(tmp_path), routing_config_digest="sha256:d",
                      queued_ms=0, fallback_reason=None)
    except Exception:
        pass
    assert seen.get("stdin") == _PROMPT
