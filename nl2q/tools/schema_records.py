"""Write the generic record types and details of the plan format (nl2q/translation/resources/schema_records.json):
every SPHN 2026.1 record type with data that plan.RECORDS does not curate, and every schema property with data that
the curated tables do not cover, so a plan can name any of them; curated entries always win. Static sources only:
compliance/domains.json (2026.1 class -> properties), the blocks of config/schema.json and config/card.json (each
property's range), the data inventory (nl2q_data/inventory: which properties carry data, the main code path) and
query_check's terms.json (which sphn: terms the 2024.2 mock data has).
  python -m nl2q.tools.schema_records
One line per class: {"spec": like a RECORDS entry (generic types only), "details": {alias: [path, kind, allowed]}};
kind "number" (a quantity: value, "<alias>_unit" its unit), "date", "code" (grouped by the code's class; allowed: its
code systems, [] when the schema names none) or "value" (a value-set individual; allowed: the individuals)."""
import json, re
from collections import Counter
from .. import schema as SC
from .. import settings as S
from ..translation import plan as P

OUT = P.HERE / "resources" / "schema_records.json"
DOMAINS = S.HERE / "compliance" / "domains.json"
CARD = S.HERE / "config" / "card.json"
INVENTORY = S.DATA / "inventory" / "inventory.json"
TERMS = S.DATA / "query_check" / "terms.json"
# The record date: the first of these a type has with data (start, determination, collection, report, record, onset,
# then a plain date-time), as the curated types pick theirs.
DATES = ("hasStartDateTime", "hasDeterminationDateTime", "hasCollectionDateTime", "hasReportDateTime",
         "hasRecordDateTime", "hasOnsetDateTime", "hasDateTime")
# Never a detail: the patient and source links, identifiers (never released), and the 2026.1 names the 2024.2 data
# has under another name (query_check.to_2024: hasRankCode, hasAdmission, hasDischarge, hasGenomicVariation).
SKIP = {"hasSubjectPseudoIdentifier", "hasSourceSystem", "hasDataProvider", "hasIdentifier", "hasSharedIdentifier",
        "hasRankCode", "hasAdmission", "hasDischarge", "hasGenomicVariation"}
BODY_SITE = "BodySite"  # a chain through a body site (hasBodySite, hasInsertionPoint) is where, not what: no code
NOT_SYSTEMS = {"sphn", "sphn-individual", "constraints"}


def ranges(blocks):
    """{(class, property): range text} from the blocks' indented lines: a depth-1 line belongs to its block's class, a
    deeper one to the single class its parent line leads to (a line leading to several classes: none)."""
    out = {}
    for head, text in blocks.items():
        owners = {0: head.split(":", 1)[1]}
        for line in text.split("\n")[1:]:
            m = re.match(r"^( *)sphn:(\w+)\s*(.*)$", line)
            if not m:
                continue
            depth, prop, rng = len(m.group(1)) // 4, m.group(2), m.group(3)
            owner = owners.get(depth - 1)
            if owner:
                out.setdefault((owner, prop), rng)
            to = re.match(r"^-> sphn:(\w+)(?: \(|$)", rng)
            owners = {k: v for k, v in owners.items() if k < depth} | {depth: to.group(1) if to else None}
    return out


def kind(rng):
    """number, date, code, value, link:<Class>, or None (strings, partial dates, several classes: never a detail)."""
    if rng.startswith("-> code"):
        return "code"
    if rng.startswith("-> sphn:Quantity"):
        return "number"
    if m := re.match(r"^-> sphn:(\w+)(?: \(|$)", rng):
        return "link:" + m.group(1)
    if rng.startswith("one of sphn-individual:"):
        return "value"
    return {"xsd:dateTime": "date", "xsd:double": "literal"}.get(rng)


def alias(prop):
    """hasSystolicPressure -> systolic_pressure, hasReactionTypeCode -> reaction_type_code."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", prop[3:] if prop.startswith("has") else prop).lower()


def systems(rng, found=()):
    """The code systems a code range allows ("code in {snomed:...}", "code under loinc:...", <IRI>), and those found
    in data (prefixed; a bare namespace IRI such as .../obo/ names no one system)."""
    named = set(re.findall(r"(?<![\w<])([a-z][a-z0-9-]*):", rng)) | {f.split(":")[0] for f in found if not f.startswith("<")}
    named |= {p for i in re.findall(r"<([^>]+)>", rng) for p, ns in SC.PREFIXES.items() if i.startswith(ns)}
    return sorted(s for s in named if s in SC.PREFIXES and s not in NOT_SYSTEMS)


def sources():
    """(2026.1 class -> properties, (class, property) -> range, class -> {properties with data}, inventory, present)."""
    props = json.loads(DOMAINS.read_text())["props"]
    rng = ranges(json.loads(CARD.read_text())["blocks"]) | ranges(SC.CFG["blocks"])  # the data-filtered card first
    inv = json.loads(INVENTORY.read_text())["record_types"]
    present = set(json.loads(TERMS.read_text())["present"])
    data = {}
    for t, e in inv.items():
        for p in e["properties_in_data"]:
            steps = p["property_path"].split("/")
            if len(steps) == 1:
                data.setdefault(t, set()).add(steps[0][5:])
            elif len(steps) == 2 and p.get("via"):
                data.setdefault(p["via"][5:], set()).add(steps[1][5:])
    return props, rng, data, inv, present


def covered(t):
    """The properties a curated type's fixed attributes, coded details and measures already name."""
    spec = P.CURATED[t]
    paths = [v for v in spec.values() if isinstance(v, str)] + [v[0] for v in P.CODED.get(t, {}).values()]
    paths += [v[0] for v in P.MEASURES.get(t, {}).values()] + [P.INSURANCE[0]]
    return {s[5:] for p in paths for s in p.split("/")} | ({"hasSubjectAge"} if spec.get("age") else set())


def chain(t, props, rng, data, inv):
    """A record type's code path: sphn:hasCode, else the data's main code path when it ends in hasCode within 3 steps,
    every step a 2026.1 property with data, not through a body site."""
    if "hasCode" in props.get(t, ()) and "hasCode" in data.get(t, ()):
        return "sphn:hasCode", t
    path = (inv.get(t) or {}).get("main_code_path") or ""
    steps, owner = [s[5:] for s in path.split("/")], t
    if not path.endswith("/sphn:hasCode") or len(steps) > 3:
        return None, None
    for s in steps[:-1]:
        k = kind(rng.get((owner, s), ""))
        if s not in props.get(owner, ()) or s not in data.get(owner, ()) or not (k or "").startswith("link:") or \
                k[5:] == BODY_SITE:
            return None, None
        owner = k[5:]
    return (path, owner) if "hasCode" in props.get(owner, ()) else (None, None)


def build():
    props, rng, data, inv, present = sources()
    results = [c for c in json.loads(DOMAINS.read_text())["sub"]["Result"] if c in present]
    types = sorted(t for t in set(inv) | set(results) if t in props and t in present and t not in P.CURATED)
    out, skipped = {}, Counter()
    for t in list(P.CURATED) + types:
        have = set(props.get(t, ())) & data.get(t, set())
        spec, fixed = {}, set()
        if t not in P.CURATED:
            code, owner = chain(t, props, rng, data, inv)
            found = [p for c in (inv.get(t) or {}).get("codes", []) if c["path"] == code for p in c["namespaces"]]
            if code and found and set(found) <= {"sphn:"}:  # only generic sphn:Code nodes (strings): no code class
                code = None
            spec |= {"code": code, "systems": systems(rng.get((owner, "hasCode"), ""), found)} if code else {"systems": []}
            if date := next((d for d in DATES if d in have), None):
                spec["date"] = "sphn:" + date
            for key, p in (("start", "hasStartDateTime"), ("end", "hasEndDateTime"), ("value", "hasQuantity"),
                           ("route", "hasAdministrationRouteCode")):
                if p in have and (key != "value" or kind(rng.get((t, p), "")) == "number"):
                    spec[key] = "sphn:" + p
            spec |= {"case": True} if "hasAdministrativeCase" in have else {}
            spec |= {"age": True} if "hasSubjectAge" in have else {}
            spec |= {"subclasses": True} if json.loads(DOMAINS.read_text())["sub"].get(t) else {}
            fixed = {"hasCode", "hasAdministrativeCase", "hasSubjectAge"} | ({"hasQuantity"} if "value" in spec else set()) | \
                {s[5:] for s in (spec.get("date"), spec.get("start"), spec.get("end"), spec.get("route")) if s}
            fixed |= {code.split("/")[0][5:]} if code else set()
        else:
            fixed = covered(t)
        taken = set(P.ALIASES) | {"insurance"} | set(P.CODED.get(t, {})) | set(P.MEASURES.get(t, {})) | \
            {a + "_unit" for a, (_, k) in P.MEASURES.get(t, {}).items() if k == "number"}
        details = {}
        for p in sorted(have - fixed - SKIP):
            k, a = kind(rng.get((t, p), "")), alias(p)
            if k is None or k.startswith("link:") or k == "literal":
                skipped[k.split(":")[0] if k else "other"] += 1
                continue
            names = [a, a + "_unit"] if k == "number" else [a]
            if any(n in taken or not re.match(r"^[a-z][a-z_]{0,30}$", n) for n in names):
                skipped["name"] += 1
                continue
            taken |= set(names)
            r = rng.get((t, p), "")
            allowed = {"value": lambda: [] if "..." in r else re.findall(r"sphn-individual:(\w+)", r),  # [] unknown
                       "code": lambda: systems(r)}.get(k)
            details[a] = ["sphn:" + p, k] + ([allowed()] if allowed else [])
        if t not in P.CURATED or details:
            out[t] = ({"spec": spec} if t not in P.CURATED else {}) | {"details": details}
    return out, skipped


def dump(data):
    """The JSON of {class: ...}, one line per class."""
    return "{\n" + ",\n".join(f"{json.dumps(t)}: {json.dumps(v)}" for t, v in data.items()) + "\n}\n"


def main():
    out, skipped = build()
    OUT.write_text(dump(out))
    generic = [t for t in out if t not in P.CURATED]
    print(f"{OUT}: {len(generic)} generic types, {sum(len(v['details']) for v in out.values())} details "
          f"({sum(len(out[t]['details']) for t in out if t in P.CURATED)} on curated types); not details: {dict(skipped)}")


if __name__ == "__main__":
    main()
