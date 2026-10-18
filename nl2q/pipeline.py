"""Request -> [d3 pre-screen] -> SPARQL (translation layer) -> compliance verdict (compliance layer). Stateless: one
request, one query, one verdict. With NL2Q_PRESCREEN=1 the d3 decision model first judges the request text alone and
can only block: a blocked request gets no query (allowed for a non-compliant request) and the reason; anything else,
d3 unreachable included, goes on to generation. The generator translates literally (it never refuses or repairs a
request); the verdict on the query comes from the deterministic rules alone. Only syntax errors and terms the schema
does not have are sent back to the generator (compliance.validate): a retry must never turn a non-compliant request
into a compliant query. This is the only file where the two layers meet: keep it small."""
import logging, time
from . import settings as S
from .compliance import judge, kanon, screen, validate
from .optimize import optimize
from .optimize.slowpath import slowpath
from .translation import generate

log = logging.getLogger(__name__)


def translate(text, llm_fn=None, decide_fn=None):
    """{sparql_query, tokens_consumed, is_compliant, compliance_reason, attempts, seconds, checker}."""
    t0, usage = time.perf_counter(), {}
    d = screen(text, decide_fn, usage) if S.PRESCREEN else None
    if d and d["blocked"]:
        query, attempts, checker, optimized = "", [], "d3", []
        reason = f"{d['description']} (judged on the request before translation, {d['confidence']:.0%})"
    else:
        query, attempts = generate(text, validate, llm_fn, usage)
        query, optimized = optimize(query)  # NL2Q_OPTIMIZE=1: same rows, faster in Virtuoso; judged as returned
        reason, checker = judge(query), "rules"
        query, optimized = slowpath(text, query, reason, optimized, llm_fn, usage)  # NL2Q_SLOWPATH: compliant stays so
        if S.KANON != "off" and reason is None:  # k-anonymity, off by default: flag or drop groups of < K patients
            query, reason = kanon(query)
            checker = "kanon" if reason else checker
    out = {"sparql_query": query, "tokens_consumed": sum(usage.values()), "is_compliant": reason is None,
           "compliance_reason": reason, "attempts": attempts, "seconds": round(time.perf_counter() - t0, 3), "checker": checker,
           "optimized": optimized}
    log.info(f"{'compliant' if reason is None else 'not compliant'} ({checker}), {len(attempts)} rejected attempt(s), "
             f"{out['tokens_consumed']} tokens, {out['seconds']} s")
    return out
