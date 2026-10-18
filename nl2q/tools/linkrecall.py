"""Offline recall of the schema card: does the prompt's card show the classes the reference query uses?
  NL2Q_LINK=0|1 python -m nl2q.tools.linkrecall [--k 6] [-v]
Gold classes come from the reference queries (tests/bench/official.jsonl, compliance.jsonl, config/examples.md): every
sphn: class they name. A gold class counts as seen when the card text names it (block header or nested line). No
database, no model."""
import argparse, json, re
from pathlib import Path
from .. import schema as SC
from ..translation import prompt as P
from ..translation.examples import load

ROOT = Path(__file__).resolve().parents[2]
SKIP = {"SubjectPseudoIdentifier", "SPHNConcept", "Code", "Terminology"}


def gold(sparql):
    classes = {SC.NS + c for c in re.findall(r"\bsphn:([A-Z]\w*)", sparql)}
    return sorted(c.split("#")[-1] for c in classes if c in SC.TERMS and c.split("#")[-1] not in SKIP)


def cases():
    out = []
    for f in ("official.jsonl", "compliance.jsonl"):
        for line in (ROOT / "tests" / "bench" / f).read_text().splitlines():
            if line.strip() and (r := json.loads(line)).get("sparql"):
                out.append((f.split(".")[0][:4] + ":" + r["id"], r["question"], r["sparql"]))
    out += [("ex:" + e["id"], e["question"], e["sparql"]) for e in load() if e["sparql"] and not e["always"]]
    return out


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--k", type=int, default=6)
    a.add_argument("-v", action="store_true", help="print the missed classes per question")
    a = a.parse_args()
    tot = hit = full = n = chars = gc = gh = 0
    for id_, q, sparql in cases():
        g = gold(sparql)
        if not g:
            continue
        text = P.card(q, k=a.k)
        seen = [c for c in g if re.search(rf"\bsphn:{c}\b", text)]
        n, tot, hit, full, chars = n + 1, tot + len(g), hit + len(seen), full + (len(seen) == len(g)), chars + len(text)
        want = set(re.findall(r"\b((?:snomed|icd-10-gm|chop|loinc|ordo):[\w.-]*\w)", sparql))
        if want:  # code IDs only: the labels are never printed
            got = [c for c, _ in P.codes_for(q, card_text=text)]
            gc, gh = gc + len(want), gh + sum(any(x == w or x.startswith(w + ".") for x in got) for w in want)
        if a.v and len(seen) < len(g):
            print(f"{id_:10} missed {sorted(set(g) - set(seen))}")
    print(f"questions {n}  gold classes {tot}  recall {hit / tot:.1%}  all-seen {full}/{n}  card {chars // n} chars avg"
          + (f"  codes found {gh}/{gc}" if gc else ""))


if __name__ == "__main__":
    main()
