"""The retry loop: ask the generator, finish the query, and retry with the feedback of `check` (compliance.validate:
syntax errors and terms the schema does not have, never compliance) at most MAX_RETRIES times.
With S.PLAN the request first goes the plan path (plan.py: a JSON plan compiled into the SPHN pattern); a request the
plan cannot express, or whose compiled query check rejects, falls back to free generation."""
import logging, re
from .. import settings as S
from . import plan as P
from .grammar import GRAMMAR
from .llm import _awake, _client, llm
from .codeclass import to_class
from .dialect import fix
from .prompt import EXAMPLES, card, codes_for, finish, messages, shots

log = logging.getLogger(__name__)

START = re.compile(r"(?im)^[ \t]*(?:#|(?:PREFIX|BASE|DEFINE|SELECT|ASK|CONSTRUCT|DESCRIBE|INSERT|DELETE|WITH|LOAD|CLEAR|DROP|"
                   r"CREATE|ADD|MOVE|COPY)\b)")


def query_only(body):
    """The query without the prose a model sometimes writes before it ("The query counts ...")."""
    m = START.search(body)
    return body[m.start():].strip() if m else body


def pointed(feedback, query):
    """A syntax error with the line it points at (and why, when the line sits outside every brace), so the model sees
    what to fix, not only a line number."""
    m = re.search(r"found '([^']*)'.*?\(line:(\d+), col:\d+\)", feedback or "")
    lines, n = query.splitlines(), int(m.group(2)) if m else 0
    if not (m and 0 < n <= len(lines) and m.group(1) in lines[n - 1]):
        return feedback
    outside = sum(l.count("{") - l.count("}") for l in lines[:n - 1]) <= 0
    return f"{feedback} -> the line is: {lines[n - 1].strip()}" + (
        " (it is outside the WHERE { ... } braces: move it inside, or remove a '}' that closes WHERE too early)" if outside else "")


def written(raw):
    """The query in a reply: its ```sparql block (else the whole reply), without prose before it."""
    m = re.search(r"```(?:sparql)?\s*(.*?)```", raw, re.S)
    return query_only((m.group(1) if m else raw).strip())


def generate(text, check, llm_fn=None, usage=None, info=None):
    """(query, attempts): the query kept (empty if the model never wrote one) and what was wrong with each rejected
    attempt. check(query) returns what to fix, or None. Tokens are added to `usage`. A dict passed as `info` gets
    info["path"]: "plan" (the plan path wrote the query) or "free", and info["fallback"]: why the plan path did not."""
    usage, info = {} if usage is None else usage, {} if info is None else info
    if S.PLAN and llm_fn is None:
        query, note = P.query(text, usage)
        if query is not None and (note := check(query)) is None:
            log.info("plan path")
            info["path"] = "plan"
            return query, []
        log.info(f"free generation ({note})")
        info["fallback"] = note
    info["path"] = "free"
    guard = {"grammar": GRAMMAR} if S.GRAMMAR and llm_fn is None else {}  # stubs in tests take (messages, usage)
    llm_fn = llm_fn or llm
    feedback, query, attempts = None, "", []
    for _ in range(S.MAX_RETRIES + 1):
        body = written(llm_fn(messages(text, feedback), usage, **guard))
        if not body:
            feedback = "the reply had no query"
        elif (feedback := pointed(check(query := to_class(fix(finish(body)), text)), query)) is None:
            break
        attempts.append(feedback)
    return query, attempts


def warm_up():
    """Before the first request: build the BM25 indexes and wake the generator if the idle watcher put it to sleep.
    NL2Q_WARM_PREFIX: then one 1-token completion of the fixed head (SYSTEM and the `always` examples), so that request 1
    reads it from vLLM's prefix cache like every later request. Its tokens belong to startup, not to a request."""
    card("warm up")
    shots("warm up")
    codes_for("warm up")
    if S.PLAN:
        P.shots("warm up")
        _awake(S.PLAN_URL)
    if S.LINK and not S.CODES.exists():
        log.warning(f"no {S.CODES}: the code lookup is off (python -m nl2q.tools.codes builds it)")
    _awake(S.LLM_URL)
    if S.WARM_PREFIX:
        m = messages("warm up")
        head = m[:1 + 2 * len(EXAMPLES)] + m[-1:]  # the shared head, then a user turn (no BM25 shots)
        try:
            _client().chat.completions.create(model=S.LLM_MODEL, messages=head, max_tokens=1, temperature=0.0, extra_body={
                "chat_template_kwargs": {"enable_thinking": S.THINK, "reasoning_effort": S.REASONING_EFFORT}})
        except Exception as e:  # never block startup: the first request then fills the cache itself
            log.warning(f"prefix warm-up failed: {e}")
