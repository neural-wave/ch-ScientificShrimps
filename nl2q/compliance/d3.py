"""Pre-screen on the request (NL2Q_PRESCREEN=1): the decision model vllm-sr/d3 (own GPU, System One API) judges the
request text alone, before any SPARQL is generated, against policy.json: proceed + one criterion per official rule, each
with 5 labelled example requests. d3 returns a probability for every option, so a block comes with a confidence.
It can only block, never approve: a request it lets through is generated and judged by the rules (rules.py), so a miss
costs nothing while a false block cannot be undone, hence the high threshold and the log-only rules (settings).
If d3 is unreachable or slower than PRESCREEN_TIMEOUT the request proceeds (fail open).
The decider (serving/serve_decision.sh, GPU 1) sleeps when idle and wakes itself on the next request, which proceeds
without it while it reloads; what it consumes is added to the request's `usage` dict, because the API reports the total
tokens of a request."""
import json, logging, time
from pathlib import Path
from urllib.request import Request, urlopen
from .. import settings as S

log = logging.getLogger(__name__)
POLICY = json.loads((Path(__file__).parent / "policy.json").read_text())


def decide(state, questions, usage):
    """System One request to the decision model: {question id: {"choice", "probabilities"}}."""
    root = S.DECISION_URL.rstrip("/")
    body = json.dumps({"state": state, "questions": questions}).encode()
    req = Request(root + "/v1/systemone", data=body, headers={"content-type": "application/json"})
    r = json.load(urlopen(req, timeout=S.PRESCREEN_TIMEOUT))
    usage["decider"] = usage.get("decider", 0) + sum((r.get("usage") or {}).values())
    return r["answers"]


def as_question(policy=POLICY):
    return {"type": "choice", "instructions": policy["instructions"],
            "criteria": {r: f"{d} Examples: " + " | ".join(policy["examples"][r]) for r, d in policy["criteria"].items()}}


def screen(text, decide_fn=None, usage=None, policy=POLICY):
    """{rule, confidence, blocked, probs, description}, or None if d3 is unreachable. Blocks when
    P(not proceed) = 1 - P(proceed) >= PRESCREEN_MIN_CONF, even if that mass is split over several rules, unless the
    top rule is in PRESCREEN_LOG_ONLY: then it is only logged."""
    t0 = time.time()
    try:
        p = (decide_fn or decide)(f"Request: {text}", {"policy": as_question(policy)}, {} if usage is None else usage)
        p = p["policy"]["probabilities"]
    except Exception as e:
        log.warning(f"decision model unreachable, the request proceeds: {str(e)[:120]}")
        return None
    probs = {r: round(float(p.get(r, 0.0)), 4) for r in policy["criteria"]}
    breach = round(1 - probs["proceed"], 4)
    top = max((r for r in probs if r != "proceed"), key=probs.get)
    over = breach >= S.PRESCREEN_MIN_CONF
    blocked = over and top not in S.PRESCREEN_LOG_ONLY
    rule = top if blocked else "proceed"
    log.info(f"d3 pre-screen: {'blocked' if blocked else 'log only' if over else 'proceed'}, "
             f"top rule {top} {breach:.1%} in {time.time() - t0:.1f} s")
    return {"rule": rule, "confidence": breach if blocked else probs["proceed"], "blocked": blocked,
            "probs": probs, "description": policy["criteria"][rule]}
