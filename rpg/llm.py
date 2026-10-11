"""Calls to a local model through the Ollama HTTP API (see framework.md, Ollama setup).

Standard library only. Every call passes a JSON schema as `format`, so the reply
is constrained to that shape and parsed here.
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = os.environ.get("RPG_MODEL", "gemma4:26b")
DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Temperatures from framework.md: low for the GM's rulings, higher for voice.
TEMP_CHARACTER = 0.8
TEMP_RESOLVE = 0.4


class LLMError(Exception):
    pass


class Client:
    def __init__(self, model=DEFAULT_MODEL, url=DEFAULT_URL, num_ctx=8192, timeout=600):
        self.model, self.url, self.num_ctx, self.timeout = model, url.rstrip("/"), num_ctx, timeout

    def chat(self, system, user, schema, temperature):
        """One non-streaming chat call. Returns (parsed JSON reply, stats dict)."""
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            # Thinking models spend hundreds of hidden tokens per call otherwise.
            "think": False,
            "format": schema,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        req = urllib.request.Request(f"{self.url}/api/chat", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            raise LLMError(f"Ollama returned {e.code}: {e.read().decode(errors='replace')[:200]}")
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"can't reach Ollama at {self.url} ({e}). Is it running?")
        content = data.get("message", {}).get("content", "")
        try:
            reply = json.loads(content)
        except json.JSONDecodeError:
            raise LLMError(f"model reply was not JSON: {content[:200]}")
        stats = {"seconds": round(data.get("total_duration", 0) / 1e9, 1),
                 "prompt_tokens": data.get("prompt_eval_count"),
                 "reply_tokens": data.get("eval_count")}
        return reply, stats
