"""Slow path (NL2Q_SLOWPATH=1, off by default): a compliant query that the cost model (cost.py) predicts slow at 100k
gets one more generator call, THINK=0, with the same messages plus feedback that names its costliest pattern with the
data's counts. The new query is kept only if it validates, is still compliant, and costs less than a fifth after the
deterministic passes; otherwise the first query stays. It is never entered for a non-compliant query, so it cannot change
a verdict, like the retries: a retry never turns a non-compliant request into a compliant query."""
import logging, re
from .. import settings as S
from . import optimize
from .cost import cost, culprit

log = logging.getLogger(__name__)


def slowpath(text, query, reason, optimized, llm_fn=None, usage=None):
    """(query, passes): the cheaper rewrite with "slowpath" added to the passes, or the query as it was ("slowpath-kept"
    when the path was tried and the rewrite refused). Entered only with NL2Q_SLOWPATH, a compliant query (reason None)
    and a predicted cost above NL2Q_SLOWPATH_T."""
    if not S.SLOWPATH or reason is not None or (c := cost(query)) is None or c <= S.SLOWPATH_T or not (hint := culprit(query)):
        return query, optimized
    from ..compliance import judge, validate
    from ..translation.generate import query_only
    from ..translation.llm import llm
    from ..translation.prompt import finish, messages
    try:
        msgs = messages(text, f"it is correct but too slow on the 100,000-patient database: {hint}. Keep the same "
                              f"question, the same results and the same returned columns")
        raw = llm_fn(msgs, usage) if llm_fn else llm(msgs, {} if usage is None else usage, think=False)
        m = re.search(r"```(?:sparql)?\s*(.*?)```", raw, re.S)
        body = query_only((m.group(1) if m else raw).strip())
        new = finish(body) if body else ""
        why = "no query" if not body else validate(new)
        if why is None:
            new, passes = optimize(new)
            why = "not compliant" if judge(new) is not None else None
            if why is None and not ((cn := cost(new)) is not None and cn < c / 5):
                why = f"cost {cn:.3g} not under a fifth of {c:.3g}"
    except Exception as e:  # the slow path must never cost the query it started from
        why = f"{type(e).__name__}: {e}"
    if why is not None:
        log.info(f"slow path kept the query (cost {c:.3g}): {why}")
        return query, optimized + ["slowpath-kept"]
    log.info(f"slow path: cost {c:.3g} -> {cost(new):.3g}")
    return new, passes + ["slowpath"]
