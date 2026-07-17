"""#428 — hooks.adversarial_review_lib.glm_complete: the public raw-text GLM wrapper the bake-off
judge uses. Confirms it routes through _glm_prepare (secret scan + credential gate + safe client
construction) and delegates the completion to _glm_attempts — never bypassing the egress guards."""
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import adversarial_review_lib as adv  # noqa: E402


def test_no_credential_returns_none_no_raise(monkeypatch):
    monkeypatch.setattr(adv, "glm_sdk_status", lambda: (True, "ok"))  # past the SDK gate
    monkeypatch.setattr(adv, "glm_api_key", lambda: None)
    payload, err = adv.glm_complete("hello")
    assert payload is None
    assert "credential" in err.lower() or "GLM" in err


def test_secret_in_prompt_is_blocked(monkeypatch):
    # client given -> skip the sdk/key/url gate; the A3 scan still runs on the outgoing prompt.
    monkeypatch.setattr(adv, "scan_for_secrets", lambda text: ["FAKE_SECRET"])
    monkeypatch.setattr(adv, "BLOCK_SECRETS", True)
    payload, err = adv.glm_complete("draft with a secret", client=object())
    assert payload is None
    assert "secret" in err.lower()


def test_delegates_to_glm_attempts(monkeypatch):
    monkeypatch.setattr(adv, "scan_for_secrets", lambda text: [])  # clean prompt
    seen = {}

    def fake_attempts(client, prompt, timeout, *, model, effort):
        seen.update(prompt=prompt, model=model, effort=effort)
        return "VERDICT-JSON", ""

    monkeypatch.setattr(adv, "_glm_attempts", fake_attempts)
    payload, err = adv.glm_complete("judge these drafts", client=object(), model="glm-5.2")
    assert (payload, err) == ("VERDICT-JSON", "")
    assert seen["prompt"] == "judge these drafts"
    assert seen["model"] == "glm-5.2"
