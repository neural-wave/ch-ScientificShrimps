"""The compliance check on mutants of the official compliant examples (mutations.jsonl): each one breaks one rule on
purpose, or stays compliant in a way that is easy to refuse by mistake. The label comes from the mutation, never from
the checker. Not under tests/bench/: a mutant has no question of its own, so it must not reach the end-to-end
evaluation. No model, no database."""
import json
from pathlib import Path
import pytest
from nl2q.compliance import analyse, reason

CASES = [json.loads(l) for l in open(Path(__file__).parent / "mutations.jsonl") if l.strip()]


@pytest.mark.parametrize("t", CASES, ids=[t["id"] for t in CASES])
def test_mutant_verdict(t):
    found = {r for r, _ in analyse(t["sparql"])[0]}
    if t["compliant"]:
        assert not found, reason(analyse(t["sparql"])[0])
    else:
        assert t["rule"] in found, reason(analyse(t["sparql"])[0]) or "judged compliant"
