"""Client for the generator: an OpenAI-compatible vLLM server (serving/serve_llm.sh, GPU 0). It adds what it consumes
to a `usage` dict, counted as prompt + completion tokens (completion includes any reasoning), because the API reports
the total tokens of a request. The server sleeps when idle (shared GPU) and is woken here.
The decider client (d3, GPU 1) is in nl2q/compliance/d3.py."""
import json, logging, os, time
from functools import lru_cache
from urllib.request import Request, urlopen
from .. import settings as S

log = logging.getLogger(__name__)
_checked = {}


def _awake(url):
    """vLLM sleep mode: wake the engine if the idle watcher put it to sleep (checked at most every 30 s)."""
    root = url.rsplit("/v1", 1)[0]
    if time.time() - _checked.get(root, 0) < 30:
        return
    _checked[root] = time.time()
    try:
        if json.load(urlopen(root + "/is_sleeping", timeout=5)).get("is_sleeping"):
            log.info("generator is asleep: waking it up")
            t0 = time.time()
            urlopen(Request(root + "/wake_up", method="POST"), timeout=600).read()
            log.info(f"generator awake in {time.time() - t0:.1f} s")
    except OSError:
        pass  # server without sleep mode (or not ours): nothing to wake


@lru_cache(4)
def _client(url=None):
    from openai import OpenAI
    return OpenAI(base_url=url or S.LLM_URL, api_key=os.getenv("OPENAI_API_KEY", "local"), timeout=S.LLM_TIMEOUT)


def llm(messages, usage, think=None, schema=None, url=None, model=None, grammar=None):
    """One chat completion. think (default S.THINK): Qwen's recommended thinking sampling; else greedy.
    vLLM's reasoning parser keeps the thoughts out of `content`, so only the final answer is parsed.
    schema: a JSON schema the answer must follow, grammar: an EBNF grammar (grammar.py, the syntax guard) — vLLM
    structured output, one or the other; url/model: another server or model (the plan writer)."""
    think = S.THINK if think is None else think
    url = url or S.LLM_URL
    _awake(url)
    t0 = time.time()
    sampling = dict(temperature=1.0, top_p=0.95, max_tokens=S.THINK_TOKENS) if think else dict(temperature=0.0, max_tokens=S.MAX_TOKENS)
    extra = {"top_k": 20, "chat_template_kwargs": {"enable_thinking": think, "reasoning_effort": S.REASONING_EFFORT}}
    if schema is not None:
        extra["structured_outputs"] = {"json": schema}
    elif grammar is not None:
        extra["structured_outputs"] = {"grammar": grammar}
    r = _client(url).chat.completions.create(model=model or S.LLM_MODEL, messages=messages, seed=S.SEED, **sampling,
                                             extra_body=extra)
    if r.usage:
        usage["prompt"] = usage.get("prompt", 0) + r.usage.prompt_tokens
        usage["completion"] = usage.get("completion", 0) + r.usage.completion_tokens
    log.info(f"generator: {r.usage.prompt_tokens if r.usage else '?'} + {r.usage.completion_tokens if r.usage else '?'} "
             f"tokens in {time.time() - t0:.1f} s")
    return r.choices[0].message.content or ""

