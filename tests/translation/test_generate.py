"""The retry loop with a stub generator and compliance.validate as the check: what is retried, with which feedback,
and that tokens add up. No model, no database."""
from nl2q import settings as S
from nl2q.compliance import validate
from nl2q.translation import generate

OK = "```sparql\nSELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:BilledDiagnosis } }\n```"
BAD = "```sparql\nSELECT (COUNT(* AS ?n) WHERE { ?s a ?t }\n```"
UNKNOWN = "```sparql\nSELECT (COUNT(DISTINCT ?g) AS ?n) WHERE { GRAPH ?g { ?dx rdf:type sphn:Nope } }\n```"


def stub(*replies):
    calls = []

    def fn(messages, usage):
        calls.append(messages)
        usage["prompt"] = usage.get("prompt", 0) + 100
        usage["completion"] = usage.get("completion", 0) + 10
        return replies[min(len(calls) - 1, len(replies) - 1)]
    fn.calls = calls
    return fn


def test_syntax_error_is_retried_with_feedback_and_tokens_add_up():
    llm, usage = stub(BAD, OK), {}
    query, attempts = generate("q", validate, llm, usage)
    assert validate(query) is None and len(llm.calls) == 2 and sum(usage.values()) == 220
    assert "previous query failed" in llm.calls[1][-1]["content"] and attempts[0].startswith("SPARQL syntax error")


def test_unknown_terms_are_retried_then_kept():
    llm = stub(UNKNOWN)
    query, attempts = generate("q", validate, llm)
    assert len(llm.calls) == S.MAX_RETRIES + 1 and "sphn:Nope" in query and "not in the SPHN schema" in attempts[-1]


def test_empty_reply_is_retried():
    llm = stub("", OK)
    query, attempts = generate("q", validate, llm)
    assert attempts == ["the reply had no query"] and validate(query) is None


def test_prose_before_the_query_is_dropped():
    llm = stub("```sparql\nThe query counts the patients.\n" + OK.split("\n", 1)[1])
    query, attempts = generate("q", validate, llm)
    assert attempts == [] and len(llm.calls) == 1 and validate(query) is None


def test_syntax_error_feedback_quotes_the_line():
    bad = "```sparql\nSELECT (COUNT(DISTINCT ?g) AS ?n)\nWHERE { GRAPH ?g { ?dx rdf:type sphn:BilledDiagnosis } }\nBIND (1 AS ?x)\n```"
    llm = stub(bad, OK)
    _, attempts = generate("q", validate, llm)
    assert "the line is: BIND (1 AS ?x)" in attempts[0] and "BIND (1 AS ?x)" in llm.calls[1][-1]["content"]
    assert "outside the WHERE" in attempts[0]
