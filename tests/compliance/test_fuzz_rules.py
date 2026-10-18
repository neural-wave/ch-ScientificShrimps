"""The fuzzer itself (nl2q/tools/fuzz_rules.py): its parser gives back a query the checker reads the same way, and
each rewrite yields valid SPARQL that is never judged compliant when its parent is not. No model, no database."""
import json
from pathlib import Path
import pytest
from nl2q.compliance import judge
from nl2q.tools.fuzz_rules import REWRITES, apply_chain, parse, render

BENCH = [json.loads(l) for f in sorted((Path(__file__).parents[1] / "bench").glob("*.jsonl")) for l in open(f) if l.strip()]


@pytest.mark.parametrize("t", BENCH, ids=[t["id"] for t in BENCH])
def test_roundtrip_keeps_the_verdict(t):
    assert judge(render(parse(t["sparql"]))) == judge(t["sparql"])


@pytest.mark.parametrize("name", sorted(REWRITES))
def test_rewrite_never_makes_a_refused_query_compliant(name):
    applied = 0
    for t in BENCH:
        q, ap = apply_chain(parse(t["sparql"]), [(name, f"test:{t['id']}")])
        if not ap:
            continue
        applied += 1
        verdict = judge(render(q))
        assert not (verdict or "").startswith("Not a valid SPARQL query for Virtuoso: SPARQL syntax"), verdict
        assert t["compliant"] or verdict is not None, f"{t['id']}: {render(q)}"
    assert applied, f"{name} never applied to the bench"
