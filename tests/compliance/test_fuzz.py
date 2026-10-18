"""The compliance check on fuzz variants (fuzz.jsonl, made with nl2q/tools/fuzz_rules.py): rewrites of labelled queries
that keep their meaning (renamed variables, aliases, inverse paths, ...), so each one inherits its parent's label
mechanically. The non-compliant ones were judged compliant before the fix their note names; the compliant ones guard
those fixes against refusing too much. No model, no database."""
import json
from pathlib import Path
import pytest
from nl2q.compliance import Invalid, analyse, judge, reason

CASES = [json.loads(l) for l in open(Path(__file__).parent / "fuzz.jsonl") if l.strip()]


@pytest.mark.parametrize("t", CASES, ids=[t["id"] for t in CASES])
def test_fuzz_verdict(t):
    verdict = judge(t["sparql"])
    if t["compliant"]:
        assert verdict is None, verdict
        return
    assert verdict is not None, "judged compliant"
    try:
        found = {r for r, _ in analyse(t["sparql"])[0]}
    except Invalid:  # refused before any rule is read (a variable name reserved by the parser)
        return
    assert t["rule"] in found, reason(analyse(t["sparql"])[0])
