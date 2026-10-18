"""The compliance check on the leaks a review found on 17 Oct (leaks.jsonl), each one judged compliant before its fix:
a node reached only through a non-sphn data property (the per-patient DataRelease node, dct:conformsTo), and a
terminology pin, coding system or Hash type that does not hold on every row (inside OPTIONAL, MINUS, FILTER NOT EXISTS
or one UNION branch). The compliant cases guard the fixes against refusing too much. No model, no database."""
import json
from pathlib import Path
import pytest
from nl2q.compliance import analyse, reason

CASES = [json.loads(l) for l in open(Path(__file__).parent / "leaks.jsonl") if l.strip()]


@pytest.mark.parametrize("t", CASES, ids=[t["id"] for t in CASES])
def test_leak_verdict(t):
    found = {r for r, _ in analyse(t["sparql"])[0]}
    if t["compliant"]:
        assert not found, reason(analyse(t["sparql"])[0])
    else:
        assert t["rule"] in found, reason(analyse(t["sparql"])[0]) or "judged compliant"
