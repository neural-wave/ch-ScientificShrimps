"""What a question must keep (Keep) and the checks: plans the benchmark intends, verify() (check, compile,
parse, compliance) and problems() (a rewrite that loses or adds a code, number, date, unit or cue)."""
import re
from ..compliance import Invalid, analyse, judge
from ..translation import plan as P
from ..translation.prompt import finish
from .tables import MONTHS, BENCH_IDS, BENCH_URLS, BENCH_GRAPHS, PROPER, WORDNUM, SYSTEM_WORDS

# ---- the benchmark's intended plans: never sampled -------------------------------------------------------------------

def bench(plan):
    """True for a plan the benchmark (tests/bench) intends, by record types, codes and shape: all patients (and their
    identifiers), the whole graph, billed diagnosis I10 counted, E84 identifiers, A00 identifiers or rows, deleting or
    counting per patient all billed diagnoses, oncology diagnoses as a graph, and its graph names, URL, identifier."""
    recs = list((plan.get("records") or {}).values())
    u = plan.get("unsafe") or {}
    if any(g in u.get("graph", "") for g in BENCH_GRAPHS) or u.get("service") in BENCH_URLS or \
            any(r.get("identifier") in BENCH_IDS for r in recs):
        return True
    if not recs:
        return not u.get("graph") and not u.get("service") and not u.get("form")
    if len(recs) != 1:
        return False
    r, plain = recs[0], not recs[0].get("where") and not recs[0].get("identifier")
    codes = r.get("code") or []
    if r["type"] == "LabResult" and u.get("service"):
        return True
    if r["type"] == "BilledDiagnosis" and plain:
        if codes == ["icd-10-gm:I10"] and not u:
            return True
        if codes == ["icd-10-gm:E84"] or codes == ["icd-10-gm:A00"] and (u.get("expose") or u.get("rows")):
            return True
        if not codes and (u.get("form") == "DELETE" or u.get("per_patient")):
            return True
    return r["type"] == "OncologyDiagnosis" and plain and not codes and u.get("form") == "CONSTRUCT"


# ---- what a question must keep ---------------------------------------------------------------------------------------

class Keep:
    """What a question must keep: exact strings (codes, graph names, identifiers, URLs), numbers, dates, plain
    names, and cues (regexes one of which must match; `forbid` ones must not)."""

    def __init__(self):
        self.exact, self.numbers, self.dates, self.names, self.cues, self.forbid = set(), set(), set(), set(), [], []

    def number(self, *xs):
        self.numbers |= {_num(x) for x in xs}

    def cue(self, label, rx):
        self.cues.append((label, rx))

    def dump(self):
        return {"exact": sorted(self.exact), "numbers": sorted(self.numbers), "dates": sorted(self.dates),
                "names": sorted(self.names), "cues": self.cues, "forbid": self.forbid}


def _num(x):
    return str(int(x)) if float(x) == int(float(x)) else str(x)


def merge(keeps):
    k = Keep()
    for x in keeps:
        k.exact |= x.exact
        k.numbers |= x.numbers
        k.dates |= x.dates
        k.names |= x.names
        k.cues += x.cues
        k.forbid += x.forbid
    return k


# ---- the benchmark's intended v2 and v2.1 plans: never sampled -------------------------------------------------------

def v2_bench(plan):
    """True for a v2 plan the benchmark intends: CHOP codes per class (O06), drug routes (O07), cholera by sex (O03),
    average age of type 2 diabetes (N03)."""
    (r,) = plan["records"].values()
    codes, group = r.get("code") or [], plan.get("group") or []
    return r["type"] == "BilledProcedure" and any("code_class" in d for d in group) or \
        r["type"] == "DrugAdministrationEvent" and any(str(d.get("code_class", "")).endswith(".route") for d in group) \
        and not codes and not r.get("where") or \
        any(c.startswith("icd-10-gm:A00") for c in codes) and any(d.get("sex") for d in group) or \
        any(c.startswith("icd-10-gm:E11") for c in codes) and any(m.get("of", "").endswith(".age") for m in plan["output"])


def v21_bench(plan):
    """True for a plan equivalent to the benchmark's: O08 (variants by variation, cancer flag), O09 (adverse events
    by consequences, outcome flags), O10 (births by delivery mode), O07 (drugs by route), O06 (CHOP 00.0 by code,
    main-rank flag), O03 (cholera main diagnosis by sex)."""
    recs, group, out = plan["records"], plan.get("group") or [], plan.get("output") or []
    types = {r["type"] for r in recs.values()}
    grouped = {str(d.get("code_class", "")).split(".")[-1] for d in group if "." in str(d.get("code_class", ""))}
    tests = {str((m.get("if") or {}).get("attr", "")).split(".")[-1] for m in out} | \
            {str(w["attr"]).split(".")[-1] for r in recs.values() for w in r.get("where") or []}
    flags = {recs[m["if"]["has"]]["type"] for m in out if "has" in (m.get("if") or {})}
    codes = [c for r in recs.values() for c in r.get("code") or []]
    return ("VariantDescriptor" in types and ("variation" in grouped | tests or "OncologyDiagnosis" in flags)) or \
        ("AdverseEvent" in types and ("consequences" in grouped or "outcome" in grouped and "consequences" in tests)) or \
        ("Birth" in types and "delivery_mode" in grouped) or \
        ("DrugAdministrationEvent" in types and grouped & {"route", "administration_route"}) or \
        ("BilledProcedure" in types and (any("code_class" in d for d in group) or any(c.startswith("chop:00.0") for c in codes))) or \
        (any(c.startswith("icd-10-gm:A00") for c in codes) and "rank" in tests | grouped and any(d.get("sex") for d in group))


# ---- verification ---------------------------------------------------------------------------------------------------

def verify(item):
    """Why a plan is rejected (None when it passes): check(), compile(), the query parses, the compliance rules agree
    with its kind (compliant when safe, not compliant when unsafe). Sets item["query"]."""
    plan = item["plan"]
    if plan["route"] == "free":
        return None
    if bad := P.check(plan):
        return "check: " + bad[0]
    try:
        query = finish(P.compile(plan))
    except P.Unsupported as e:
        return f"compile: {e}"
    try:
        analyse(query)
    except Invalid as e:
        return f"parse: {str(e)[:80]}"
    reason = judge(query)  # the deterministic rules (compliance.judge)
    if item["kind"] == "unsafe" and reason is None:
        return "unsafe plan judged compliant"
    if item["kind"] == "safe" and reason is not None:
        return "safe plan judged not compliant: " + reason[:80]
    item["query"] = query
    return None


def empty(res):
    """No answer: no row, or only zeros and missing values."""
    return not res.get("rows") or all(v in (None, "0", "0.0") for v in res["rows"][0])


# ---- rewrites --------------------------------------------------------------------------------------------------------

def normalize(text):
    """Month-name dates as YYYY-MM-DD, number words before time units as digits."""
    t = text.replace("‑", "-").replace("–", "-").replace("−", "-").replace(" ", " ")
    mon = "|".join(MONTHS) + "|" + "|".join(m[:3] for m in MONTHS) + "|sept"
    m2n = lambda s: [m[:3] for m in MONTHS].index(s.lower()[:3]) + 1
    t = re.sub(rf"\b({mon})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
               lambda m: f"{m[3]}-{m2n(m[1]):02d}-{int(m[2]):02d}", t, flags=re.I)
    t = re.sub(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({mon})\.?,?\s+(\d{{4}})\b",
               lambda m: f"{m[3]}-{m2n(m[2]):02d}-{int(m[1]):02d}", t, flags=re.I)
    t = re.sub(r"\b(\d{4})/(\d{2})/(\d{2})\b", r"\1-\2-\3", t)
    t = re.sub(rf"\b({'|'.join(WORDNUM)})[\s-]+(day|week|month|year)s?\b", lambda m: f"{WORDNUM[m[1].lower()]} {m[2]}",
               t, flags=re.I)
    return t


def numbers(text, exact):
    """The numbers a text states, once codes, graph names, identifiers, URLs, dates and units are set aside."""
    for s in sorted(exact, key=len, reverse=True):
        text = text.replace(s, " ")
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    text = re.sub(SYSTEM_WORDS, " ", text, flags=re.I)
    text = re.sub(r"(\d)\s*-\s*(\d)", r"\1 to \2", text)
    return {_num(x) for x in re.findall(r"(?<![\w.^*/-])\d+(?:\.\d+)?(?![\w])", text)}


def problems(text, item):
    """Why a rewrite does not ask the same as the template ([] when it does)."""
    keep, template = item["keep"], item["template"]
    t, bad = normalize(text), []
    if not 12 <= len(t) <= 700 or "{" in t or "rewrite" in t.lower():
        return ["length or format"]
    for s in keep["exact"]:
        rx = rf"(?<![\w.]){re.escape(s)}(?!\w|\.\d)" if re.match(r"^[\w.-]+$", s) else re.escape(s)
        if not re.search(rx, t):
            kind = "URL" if "://" in s else "graph name" if "mock_data" in s else \
                "code" if any(re.match(rx_, s) for rx_ in PROPER.values()) else "identifier or unit"
            bad.append(f"lost {kind}: {s}")
    for d in keep["dates"]:
        if d not in t:
            bad.append(f"lost date: {d}")
    want, have = set(keep["numbers"]), numbers(t, set(keep["exact"]))
    allowed = want | numbers(template, set(keep["exact"])) | {n for s in keep["names"] for n in re.findall(r"\d+", s)}
    bad += [f"lost number: {n}" for n in sorted(want - have)]
    bad += [f"added number: {n}" for n in sorted(have - allowed)]
    new_codes = set(re.findall(r"\b[A-Z]\d\d(?:\.\d{1,2})?\b", t)) - set(keep["exact"]) - \
        set(re.findall(r"\b[A-Z]\d\d(?:\.\d{1,2})?\b", template))
    bad += [f"added code: {c}" for c in sorted(new_codes)]
    for name in keep["names"]:
        words = [w[:5] for w in re.findall(r"[a-z]{4,}", name.lower()) if w not in ("with", "without", "other", "unspecified",
                                                                                  "type", "disease", "due")]
        if words and sum(w in t.lower() for w in words) < max(1, len(words) // 2):
            bad.append(f"lost name: {name}")
    for label, rx in keep["cues"]:
        if rx and not re.search(rx, t, re.I):
            bad.append(f"lost cue ({label})")
    for rx in keep["forbid"]:
        if not re.search(rx, template, re.I) and re.search(rx, t, re.I):
            bad.append("strict became inclusive")
    return bad
