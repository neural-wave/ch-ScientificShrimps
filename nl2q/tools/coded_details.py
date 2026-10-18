"""Write the coded details of each plan record type (nl2q/translation/resources/coded.json) from the SPHN 2026.1
schema: the properties whose value is a code node (config/schema.json code_props: grouped by the code's class) or a
value-set individual (value_props: grouped by the individual), named for the plan as the property without "has" and
"Code" in snake case (hasZygosityCode -> zygosity, hasOutcome -> outcome).
  python -m nl2q.tools.coded_details        # needs the 2026.1 schema file (NL2Q_SCHEMA_TTL)"""
import json, re
from rdflib import URIRef
from .. import schema as SC
from ..translation import plan as P
from .check_examples import SCHEMA_TTL, Schema, short

OUT = P.HERE / "resources" / "coded.json"
# Linked records grouped by their SPHN class ("class"): the organizers' example 8 groups genetic variants by the class
# of the variation a descriptor links to (sphn:GenomicDeletion, sphn:SingleNucleotideVariation, ...).
CLASS_OF = {"VariantDescriptor": {"variation": ("sphn:hasGenomicVariation", "sphn:GenomicVariation")}}  # (link, its generic class)


def alias(prop):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", re.sub(r"^has|Code$", "", str(prop).rsplit("#", 1)[1])).lower()


def details(schema):
    out = {}
    for t in P.CURATED:
        c = URIRef(SC.NS + t)
        found = {alias(p): [short(p), "code"] for p in SC.CODE_PROPS
                 if schema.domain[p] and schema.has(c, p) and str(p) != SC.NS + "hasCode"}
        found |= {alias(p): [short(p), "value"] for p in SC.VALUE_PROPS if schema.domain[p] and schema.has(c, p)}
        found |= {a: [p, "class", generic] for a, (p, generic) in CLASS_OF.get(t, {}).items()}
        out[t] = dict(sorted(found.items()))
    return out


def dump(data):
    """The JSON of {record type: ...}, one line per record type."""
    return "{\n" + ",\n".join(f"{json.dumps(t)}: {json.dumps(v)}" for t, v in data.items()) + "\n}\n"


def main():
    OUT.write_text(dump(details(Schema(SCHEMA_TTL))))
    print(f"{OUT}: {sum(map(len, json.loads(OUT.read_text()).values()))} coded details")


if __name__ == "__main__":
    main()
