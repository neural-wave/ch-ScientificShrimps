"""The two entry points of the compliance layer, re-exported by nl2q.compliance."""
import logging
from .. import settings as S
from .feedback import feedback, sql
from .rules import Invalid, analyse, reason

log = logging.getLogger(__name__)


def validate(query):
    """What the generator must fix before the query is judged: a syntax error, or sphn: terms the schema does not
    have; with NL2Q_LINK on also a property the class lacks, SQL words, a code used as a value, an unbound variable
    (feedback.py), on a query the rules accept. None when there is nothing to fix. It never mentions compliance: a
    retry must not turn a non-compliant request into a compliant query."""
    try:
        violations, unknown = analyse(query)
    except Invalid as e:
        hint = S.LINK and sql(query)  # CASE WHEN, IS NULL, LEAST(: a syntax error, said precisely
        return (f"{hint}. {e}" if hint else str(e))[:300]
    except Exception:  # a bug in the checker is not the generator's to fix: judge() refuses the query
        log.exception("compliance check failed while validating")
        return None
    if unknown:
        return f"not in the SPHN schema: {', '.join(sorted(unknown))}"
    # not on a query the rules refuse: "?g is never bound" on COUNT(DISTINCT ?g) ... GRAPH <one patient> would get
    # GRAPH ?g back on the retry, a compliant query for a non-compliant request
    return feedback(query) if S.LINK and not violations else None


def judge(query):
    """compliance_reason from the deterministic rules, None when the query is compliant."""
    if not query.strip():
        return "Not a valid SPARQL query for Virtuoso: no query was generated."
    try:
        violations, _ = analyse(query)
    except Invalid as e:
        return f"Not a valid SPARQL query for Virtuoso: {str(e)[:300]}."
    except Exception as e:  # fail closed: a bug in the checker must neither approve the query nor make the API fail
        log.exception("compliance check failed")
        return f"Not judged compliant: the compliance check could not analyse this query ({type(e).__name__})."
    return reason(violations) if violations else None
