"""The worked examples (config/examples.md, written by tools/make_examples from official material): a question, its
SPHN 2026.1 query and the verdict it should get. The prompt shows the `always` ones with every request and adds the most
similar of the others (prompt.shots)."""
import re
from .. import settings as S


def load(path=None):
    """[{id, family, source, topic, kind, compliant, rule, reason, question, sparql, always, ...}] in file order;
    original_question, changes, note and sparql_mock only where the file has them."""
    out = []
    for block in re.split(r"(?m)^## ", (path or S.EXAMPLES).read_text())[1:]:
        head, _, rest = block.partition("\n")
        id_, topic, kind, *more = [f.strip() for f in head.split("|")]
        line = lambda key: m.group(1).strip() if (m := re.search(rf"(?m)^{key}:\s*(.+)$", rest)) else None
        queries = {mock.strip(): q.strip() for mock, q in re.findall(r"```sparql( mock)?\n(.*?)```", rest, re.S)}
        rule = next((f for f in more if re.fullmatch(r"R[1-5]", f)), None)
        e = {"id": id_, "family": next((f.split("=", 1)[1] for f in more if f.startswith("family=")), id_),
             "source": line("Source"), "topic": topic, "kind": kind,
             "compliant": rule is None, "rule": rule, "reason": line("Why"), "question": line("Q"),
             "sparql": queries.get(""), "always": "always" in more}
        for key, field in (("Original", "original_question"), ("Changes", "changes"), ("Note", "note")):
            if (v := line(key)) is not None:
                e[field] = v
        if "mock" in queries:
            e["sparql_mock"] = queries["mock"]
        out.append(e)
    return out
