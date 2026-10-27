"""Ask an LLM server the user already runs (any OpenAI-compatible /chat/completions endpoint:
Ollama, LM Studio, llama.cpp's llama-server, vLLM, or a cloud API) to judge the clip candidates.

No model ships with cliptool. The API key, if any, comes from the CLIPTOOL_JUDGE_KEY environment
variable and is never written to disk.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .clipfind import load_ranking_text

KEY_ENV = "CLIPTOOL_JUDGE_KEY"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
MAX_REPLY = 5_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it could carry the prompt and the key to another host."""

    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def check_url(url: str) -> str:
    """The base URL, validated (http/https with a host). Returns it without a trailing slash."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("The judge URL must look like http://127.0.0.1:11434/v1")
    return url.strip().rstrip("/")


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def ask(prompt: str, url: str, model: str, timeout: float = 600.0, key: str | None = None) -> list:
    """Send the judge prompt; return the parsed ranking list."""
    base = check_url(url)
    if not model.strip():
        raise ValueError("Name the model to use, e.g. qwen3:8b")
    body = json.dumps({
        "model": model.strip(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = (key or os.environ.get(KEY_ENV, "")).strip()  # a key typed in the UI wins; neither is stored
    if key and (base.startswith("https://") or is_local(base)):  # never send a key in clear over the network
        headers["Authorization"] = f"Bearer {key}"
    endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as res:  # scheme checked above
            data = json.loads(res.read(MAX_REPLY).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise RuntimeError(f"The judge server redirected to {e.headers.get('Location')}; "
                               "use that address directly.") from None
        detail = e.read(2000).decode("utf-8", "replace")[:300]
        raise RuntimeError(f"The judge server said {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Couldn't reach the judge server at {base}: {getattr(e, 'reason', e)}") from None
    except json.JSONDecodeError:
        raise RuntimeError("The judge server didn't answer with JSON.") from None
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unexpected reply from the judge server (no choices[0].message).") from None
    return load_ranking_text(_THINK.sub("", text), "The judge's reply")
