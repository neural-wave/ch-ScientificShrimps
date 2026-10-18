"""Compliance layer: is a request or a generated SPARQL query allowed (Query_Compliance.md), and if not, why.
The rest of nl2q calls only these functions (pipeline.py), so their signatures are the contract with the
translation layer:
- screen(text, decide_fn, usage): optional d3 pre-screen of the request before generation (NL2Q_PRESCREEN=1), can
  only block: {rule, confidence, blocked, probs, description}, or None when d3 is unreachable;
- validate(query): what the generator must fix (syntax error, terms the schema does not have; with NL2Q_LINK on, on a
  query the rules accept, also a property the class lacks, SQL words, a code used as a value, an unbound variable), or None;
- judge(query): compliance_reason, or None when the query is compliant (the rules alone);
- kanon(query): (sparql_query, compliance_reason) for a compliant query with NL2Q_KANON on (off by default): flag
  groups of fewer than NL2Q_K patients, or rewrite the query so Virtuoso drops them.
Inside: rules.py (deterministic rules, R1-R5), d3.py + policy.json (request pre-screen), verdict.py, kanon.py,
feedback.py + domains.json (validate()'s schema feedback; python -m nl2q.compliance.domains rebuilds the JSON)."""
from .d3 import screen
from .rules import RULES, Invalid, analyse, reason
from .verdict import judge, validate
from .kanon import kanon
