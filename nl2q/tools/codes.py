"""Build the static code index nl2q_data/codes.json from the terminology files (no database):
  python -m nl2q.tools.codes [--dir ONTOLOGIES] [--out nl2q_data/codes.json]
For every class of SNOMED CT, ICD-10-GM, CHOP, LOINC and ORDO: its prefixed code, its labels and its named parents
(so a search can stay under the value-set root a property allows). The labels come from restricted terminologies:
the file is gitignored, read only by the local generator, never committed or printed (counts only)."""
import argparse, json, re
from pathlib import Path
from .. import schema as SC

FILES = ("snomed-ct-*.ttl", "sphn_icd-10-gm_*.ttl", "sphn_chop_*.ttl", "sphn_loinc_*.ttl", "sphn_ordo_*.ttl")
TERM = r'<[^>\s]+>|[A-Za-z][\w-]*:[^\s;,()\[\]]*'
LABEL = re.compile(r'(?:rdfs:label|skos:(?:pref|alt)Label|<http://www\.w3\.org/2004/02/skos/core#(?:pref|alt)Label>)\s+'
                   r'((?:"(?:[^"\\]|\\.)*"(?:@[\w-]+|\^\^\S+)?\s*,?\s*)+)')
PARENT = re.compile(rf'rdfs:subClassOf\s+((?:(?:{TERM})\s*,?\s*)+)')
CLASS = re.compile(r'\s(?:a|rdf:type)\s+(?:rdfs:Class|owl:Class)\b')


def statements(path):
    """(prefixes, text) per top-level statement, streamed: the files are too large for rdflib."""
    pre, buf = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not buf:
                if (m := re.match(r"@?prefix\s+([\w-]*):\s*<([^>]*)>", line, re.I)):
                    pre[m.group(1)] = m.group(2)
                    continue
                if not line.strip() or line[0].isspace() or line[0] == "#":
                    continue
            buf.append(line)
            if line.rstrip().endswith(" .") or line.strip() == ".":
                yield pre, "".join(buf)
                buf = []


def intersection(text):
    """Named members of owl:intersectionOf ( ... ) at depth 0: the parents of a fully defined SNOMED concept."""
    out = []
    for m in re.finditer(r"owl:intersectionOf\s*\(", text):
        depth, i = 0, m.end()
        for t in re.finditer(rf"\[|\]|\)|{TERM}", text[i:]):
            s = t.group(0)
            if s == "[":
                depth += 1
            elif s == "]":
                depth -= 1
            elif s == ")" and depth == 0:
                break
            elif depth == 0:
                out.append(s)
    return out


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dir", default="/data/scratch/nw-data/virtuoso-release/ontologies")
    a.add_argument("--out", default="nl2q_data/codes.json")
    a = a.parse_args()
    ours = sorted(((ns, p) for p, ns in SC.PREFIXES.items() if p in ("snomed", "icd-10-gm", "chop", "loinc", "ordo")),
                  key=lambda x: -len(x[0]))

    def short(term, pre):
        iri = term[1:-1] if term.startswith("<") else (pre.get(term.split(":", 1)[0], "\0") + term.split(":", 1)[1])
        for ns, p in ours:
            if iri.startswith(ns) and "/" not in (loc := iri[len(ns):]) and loc:
                return f"{p}:{loc}"
        return None

    codes, labels, parents = [], [], []
    for pattern in FILES:
        for path in sorted(Path(a.dir).glob(pattern)):
            n = 0
            for pre, text in statements(path):
                if not CLASS.search(text.split(";", 1)[0]) or not (code := short(text.split(None, 1)[0], pre)):
                    continue
                labs = [re.sub(r'\\(.)', r'\1', l) for m in LABEL.finditer(text) for l in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))]
                ps = [x for m in PARENT.finditer(text) for x in re.findall(TERM, m.group(1))] + intersection(text)
                codes.append(code), labels.append(" | ".join(dict.fromkeys(labs)))
                parents.append(sorted({s for x in ps if (s := short(x, pre)) and s != code}))
                n += 1
            print(f"{path.name:40} {n:>8} classes")
    idx = {c: i for i, c in enumerate(codes)}
    out = {"codes": codes, "labels": labels, "parents": [[idx[p] for p in ps if p in idx] for ps in parents]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(codes)} codes, {sum(map(bool, labels))} with labels, {sum(map(bool, out['parents']))} with parents -> {a.out} "
          f"(restricted terminologies: never commit)")


if __name__ == "__main__":
    main()
