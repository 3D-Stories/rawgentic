# /// script
# requires-python = ">=3.10"
# dependencies = ["zhipuai>=2.1.5", "sniffio"]
# ///
"""Standalone zhipuai worker (the live dependency boundary).

Reads a request JSON on stdin: {model, prompt, max_tokens?, temperature?, effort?}.
Does a NON-streaming chat.completions.create (the non-streaming response exposes both the
provider-reported `.model` and `.usage` — verified live 2026-07-16; the streaming path used
elsewhere in the repo exposes neither). Prints the response as JSON on stdout.

Run standalone via `uv run zhipuai_call.py` (uses the PEP 723 deps above), or from the package's
locked env via `uv run --locked --extra glm python zhipuai_call.py` (deps ignored, project lock
used). `sniffio` is declared because zhipuai 2.1.5 imports it without declaring it (metadata bug).
"""
import json
import os
import sys


def main() -> int:
    req = json.load(sys.stdin)
    key = os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("ZHIPU_API_KEY") or os.environ.get("GLM_API_KEY")
    if not key:
        json.dump({"error": "no ZHIPUAI_API_KEY/ZHIPU_API_KEY/GLM_API_KEY in env"}, sys.stdout)
        return 2
    from zhipuai import ZhipuAI  # noqa: PLC0415 (worker: dep provided by uv env)

    kwargs = {"api_key": key}
    base = os.environ.get("ZHIPUAI_BASE_URL") or os.environ.get("GLM_BASE_URL")
    if base:
        kwargs["base_url"] = base
    client = ZhipuAI(**kwargs)
    resp = client.chat.completions.create(
        model=req["model"],
        messages=[{"role": "user", "content": req["prompt"]}],
        max_tokens=int(req.get("max_tokens", 1024)),
        temperature=float(req.get("temperature", 0.2)),
        stream=False,
    )
    out = resp.model_dump() if hasattr(resp, "model_dump") else resp
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
