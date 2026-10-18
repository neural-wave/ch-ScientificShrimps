"""The v1 plan samplers: conditions and records from the inventory, patient cohorts (and / or / not / before),
record counts and dates, plans with unsafe parts, free requests. sample_safe, sample_unsafe and sample_free return
(plan, template, Keep, meta)."""
import json, math, re, time
from collections import Counter
from ..translation import plan as P
from .tables import (TYPES, NO_VALUE, SEXES, LOINC, VALUES, UNIT_CUES, AGES, GAPS, OPS, CUE_OPS, INCLUSIVE, HOSTS,
    PATHS, UNSAFE_KINDS, CUES, MEAS, FREE, FREE_AVOID)
from .filters import Keep, _num, merge
from .wording import ucum_text, code_phrase, noun, describe, phrase_has, count_question

# ---- sampling -------------------------------------------------------------------------------------------------------

def _value(x):
    return int(x) if float(x) == int(float(x)) else float(x)


def weighted(rng, items):
    """One of [(item, weight)]."""
    total = sum(w for _, w in items)
    x = rng.random() * total
    for item, w in items:
        x -= w
        if x <= 0:
            return item
    return items[-1][0]


def _date_in(rng, lo, hi, recent=True):
    """A date (YYYY-MM-DD) between lo and hi (years or dates), recent years more likely for long ranges."""
    lo, hi = (lo + "-01-01" if len(lo) == 4 else lo), (hi + "-12-31" if len(hi) == 4 else hi)
    y0, y1 = int(lo[:4]), int(hi[:4])
    if y1 - y0 >= 10 and recent:
        y0, y1 = max(y0, 2000), min(y1, 2025)
    if y1 - y0 >= 2:
        y = rng.randint(y0 + 1, y1 - 1)
        return f"{y}-{rng.choice((1, 1, 1, 3, 4, 6, 7, 9, 10)):02d}-{rng.choice((1, 1, 1, 10, 15, 20)):02d}"
    d0 = time.mktime(time.strptime(lo[:10], "%Y-%m-%d"))
    d1 = time.mktime(time.strptime(hi[:10], "%Y-%m-%d"))
    return time.strftime("%Y-%m-%d", time.localtime(d0 + (d1 - d0) * rng.uniform(0.2, 0.8)))


def condition(rng, inv, rtype, name, attr, keep, code=None):
    """([where items], phrase) for one condition on a record's attribute (code: the record's code, for the data's
    value quantiles of that code)."""
    dverb = TYPES[rtype][3]
    if attr == "age":
        if rng.random() < 0.25:
            lo = rng.choice((18, 20, 30, 40, 45, 50))
            hi = rng.choice([a for a in (40, 50, 60, 64, 65, 70, 75, 80) if a >= lo + 10])
            keep.number(lo, hi)
            keep.cue("age range", r"between|from|\bto\b|-|–|and")
            return [{"attr": f"{name}.age", "op": ">=", "value": lo}, {"attr": f"{name}.age", "op": "<=", "value": hi}], \
                rng.choice((f"at an age between {lo} and {hi}", f"when the patient was aged {lo} to {hi}"))
        op, a = rng.choice((">=", ">", "<", "<=")), rng.choice(AGES)
        keep.number(a)
        phrase = {">=": (f"at age {a} or older", f"when the patient was {a} or older"),
                  ">": (f"at an age over {a}", f"when the patient was older than {a}"),
                  "<": (f"at an age under {a}", f"when the patient was younger than {a}"),
                  "<=": (f"at age {a} or younger", f"when the patient was {a} or younger")}[op]
        keep.cue(f"age {op}", CUE_OPS[op] + (r"|or older" if op == ">=" else r"|older" if op == ">" else
                                            r"|younger" if op == "<" else r"|or younger"))
        if op in (">", "<"):
            keep.forbid.append(INCLUSIVE)
        return [{"attr": f"{name}.age", "op": op, "value": a}], rng.choice(phrase)
    if attr == "date":
        lo, hi = inv[rtype]["dates"] or ("2005", "2024")
        y0, y1 = int(lo[:4]), int(hi[:4])
        form = rng.random()
        if form < 0.3 and y1 - y0 >= 3:
            y = int(_date_in(rng, lo, hi)[:4])
            if rng.random() < 0.4:
                keep.number(y)
                keep.cue("year", rf"\b{y}\b")
                return [{"attr": f"{name}.date", "op": ">=", "value": f"{y}-01-01"},
                        {"attr": f"{name}.date", "op": "<", "value": f"{y + 1}-01-01"}], f"{dverb} in {y}"
            op = rng.choice((">=", "<"))
            keep.number(y)
            keep.cue(f"date {op}", r"since|from|or later|onwards|after|starting|on or after|as of|beginning" if op == ">="
                     else r"before|prior|earlier|until|up to|till")
            return [{"attr": f"{name}.date", "op": op, "value": f"{y}-01-01"}], \
                (f"{dverb} in {y} or later" if op == ">=" else f"{dverb} before {y}")
        if form < 0.55:
            d1, d2 = sorted({_date_in(rng, lo, hi), _date_in(rng, lo, hi)})[0], None
            for _ in range(10):
                d2 = _date_in(rng, lo, hi)
                if d2 > d1:
                    break
            if d2 and d2 > d1:
                keep.dates |= {d1, d2}
                keep.cue("date range", r"between|from|\bto\b|and")
                return [{"attr": f"{name}.date", "op": ">=", "value": d1}, {"attr": f"{name}.date", "op": "<=", "value": d2}], \
                    f"{dverb} between {d1} and {d2}"
        d, op = _date_in(rng, lo, hi), rng.choice((">=", ">=", "<", ">", "<="))
        keep.dates.add(d)
        keep.cue(f"date {op}", {">=": r"on or after|since|from|starting|as of|onwards|or later|beginning",
                                ">": r"after|later than|since|following", "<": r"before|prior|earlier than|until|up to|till",
                                "<=": r"on or before|until|up to|no later than|by|till|or earlier|through"}[op])
        return [{"attr": f"{name}.date", "op": op, "value": d}], \
            {">=": f"{dverb} on or after {d}", ">": f"{dverb} after {d}", "<": f"{dverb} before {d}",
             "<=": f"{dverb} on or before {d}"}[op]
    values, units = VALUES[rtype]
    v, op = rng.choice(values), rng.choice((">", ">", ">=", "<", "<="))
    unit = rng.choice(units) if rng.random() < (0.75 if rtype in ("LabTestEvent", "LabResult") else 0.6) else None
    pairs = [p for p in inv[rtype].get("pairs") or [] if code and p[0] == code]
    if pairs and rng.random() < 0.5:  # a (code, unit) pair of the data, with one of its quantiles
        _, u, p10, median, p90, _ = rng.choice(pairs)
        op = rng.choice((">", ">=", "<", "<="))
        v = {">": p10, ">=": median, "<": p90, "<=": median}[op]
        unit = (u, ucum_text(u))
    keep.number(v)
    keep.cue(f"value {op}", CUE_OPS[op])
    if op in (">", "<"):
        keep.forbid.append(INCLUSIVE)
    items = ([{"attr": f"{name}.unit", "op": "=", "value": unit[0]}] if unit else []) + \
            [{"attr": f"{name}.value", "op": op, "value": _value(v)}]
    shown = _num(v) + (("" if unit[1] in ("%",) else " ") + unit[1] if unit else "")
    if unit:
        if unit[1] in UNIT_CUES:
            keep.cue(f"unit {unit[1]}", UNIT_CUES[unit[1]])
        else:
            keep.exact.add(unit[1])
    form = rng.choice(OPS[op])
    text = form.format(v=shown) if "{v}" in form else f"{form} {shown}"
    noun = "a result" if rtype in ("LabTestEvent",) else "a value"
    return items, (f"with {noun} {text}" if rtype in ("LabTestEvent", "LabResult") or rng.random() < 0.4 else text)


def record(rng, inv, pool, names, rtype, name, cond_p=0.45, allow=("code", "date", "age", "value"), code_p=None):
    """(record spec, words after the noun, Keep) for one record of rtype."""
    keep, spec, parts = Keep(), {"type": rtype}, []
    data = inv[rtype]["data"]
    if rtype == "AdministrativeSex":
        code, word = weighted(rng, [(SEXES[0], 5), (SEXES[1], 5), (SEXES[2], 1)])
        spec["code"] = [code]
        if rng.random() < 0.25:
            keep.exact.add(code.split(":")[1])
            return spec, (word, f"(administrative sex SNOMED CT code {code.split(':')[1]})"), keep
        keep.cue("sex", {"female": r"female|wom[ae]n|girls?\b", "male": r"\bmale|\bm[ae]n\b|boys?\b"}.get(
            word, r"indeterminate|undetermined|not determined|cannot be determined"))
        return spec, (word, ""), keep
    codes = pool.get(rtype) or []
    spec_rec = P.CURATED[rtype]
    want_code = code_p if code_p is not None else (0.8 if "code" in data else 0.0)
    if "code" in allow and "code" in spec_rec and codes and rng.random() < want_code:
        if rtype == "LabTestEvent" and rng.random() < 0.06:
            chosen = [f"loinc:{rng.choice(LOINC)}"]
        else:
            top = codes[:60]
            items = [(c, math.sqrt(n) + 1) for c, n in top]
            chosen = [weighted(rng, items)]
            if rng.random() < 0.12 and len(top) > 3:
                other = weighted(rng, [x for x in items if x[0] != chosen[0] and x[0].split(":")[0] == chosen[0].split(":")[0]])
                chosen.append(other)
        spec["code"] = chosen
        phrase, named = code_phrase(rng, chosen, keep, names)
        parts.append(phrase)
    attrs = [a for a in ("date", "value", "age") if a in allow and spec_rec.get(a) and not (a == "value" and rtype in NO_VALUE)]
    good = [a for a in attrs if a in data]
    where = []
    if attrs and rng.random() < cond_p:
        pool_ = good if good and rng.random() < 0.9 else attrs
        for a in rng.sample(pool_, min(len(pool_), 2 if rng.random() < 0.2 else 1)):
            items, phrase = condition(rng, inv, rtype, name, a, keep, (spec.get("code") or [None])[0])
            where += items
            parts.append(phrase)
    if where:
        spec["where"] = where
    return spec, ("", " ".join(p for p in parts if p)), keep


def pick_types(rng, inv, n, exclude=()):
    """n record types, weighted toward those with data (AdministrativeSex only as a partner)."""
    items = [(t, TYPES[t][4] * (1.0 if inv[t]["patients"] is None or inv[t]["patients"] > 0 else 0.15))
             for t in P.CURATED if t not in exclude]
    out = []
    while len(out) < n:
        t = weighted(rng, items)
        if t not in out or t in ("BilledDiagnosis", "LabTestEvent") and out.count(t) < 2:
            out.append(t)
    return out


def names_for(types):
    """Record variable names: dx, lab, ... and dx2 for a second record of a type."""
    out, seen = [], Counter()
    for t in types:
        seen[t] += 1
        out.append(TYPES[t][0] + (str(seen[t]) if seen[t] > 1 else ""))
    return out


def cohort_records(rng, inv, pool, names, shape):
    """(records, cohort, clause after 'patients who', Keep, types) of a patient-count shape."""
    n = {"one": 1, "and": rng.choice((2, 2, 2, 3)), "or": rng.choice((2, 2, 3)), "not": 1, "exclude": 2, "before": 2,
         "nested": 3, "neither": 2}[shape]
    exclude = () if shape in ("and", "exclude", "one") else ("AdministrativeSex",)
    types = pick_types(rng, inv, n, exclude)
    if shape == "and" and rng.random() < 0.25 and "AdministrativeSex" not in types:
        types[-1] = "AdministrativeSex"
    if shape == "before":
        types = pick_types(rng, inv, 2, exclude=[t for t in P.CURATED if not P.CURATED[t].get("date")])
    vars_ = names_for(types)
    recs, words, keeps = {}, [], []
    for t, v in zip(types, vars_):
        spec, w, k = record(rng, inv, pool, names, t, v, cond_p=0.5 if n == 1 else 0.25)
        recs[v], words = spec, words + [w]
        keeps.append(k)
    if len({json.dumps(s, sort_keys=True) for s in recs.values()}) < len(recs):  # the same record twice: again
        return cohort_records(rng, inv, pool, names, shape)
    keep = merge(keeps)
    has = [phrase_has(rng, t, w) for t, w in zip(types, words)]
    obj = [describe(rng, t, w) if t != "AdministrativeSex" else w[0] for t, w in zip(types, words)]
    if shape == "one":
        return recs, {"has": vars_[0]}, has[0], keep, types
    if shape == "and":
        if "AdministrativeSex" in types:
            i = types.index("AdministrativeSex")
            rest = [h for j, h in enumerate(has) if j != i]
            clause = f"{has[i]} and " + " and ".join(rest) if len(rest) < 2 else f"{has[i]} and " + ", ".join(rest[:-1]) + " and " + rest[-1]
        elif n == 2:
            clause = rng.choice((f"have both {obj[0]} and {obj[1]}", f"{has[0]} and also {obj[1]}",
                                 f"{has[0]} as well as {obj[1]}"))
        else:
            clause = f"have {obj[0]}, {obj[1]} and {obj[2]}"
        keep.cue("and", r"\band\b|both|as well|also|plus|together|along|combined|all three|all of|in addition|together with|&|,")
        return recs, {"and": [{"has": v} for v in vars_]}, clause, keep, types
    if shape == "or":
        clause = (rng.choice((f"have {obj[0]} or {obj[1]}", f"have either {obj[0]} or {obj[1]}")) if n == 2 else
                  f"have at least one of {obj[0]}, {obj[1]} or {obj[2]}")
        keep.cue("or", r"\bor\b|either|any of|at least one|one of")
        return recs, {"or": [{"has": v} for v in vars_]}, clause, keep, types
    neg = r"\bnot\b|\bno\b|without|never|n't|exclud|except|lack|free of|absen|neither|none|other than"
    if shape == "not":
        clause = rng.choice((f"do not have {obj[0]}", f"have never had {obj[0]}", f"have no record of {obj[0]}"))
        keep.cue("not", neg)
        return recs, {"not": {"has": vars_[0]}}, clause, keep, types
    if shape == "neither":
        keep.cue("not", neg)
        return recs, {"not": {"or": [{"has": v} for v in vars_]}}, f"have neither {obj[0]} nor {obj[1]}", keep, types
    if shape == "exclude":
        clause = rng.choice((f"have {obj[0]} but not {obj[1]}", f"{has[0]} and no {noun(rng, types[1], article=False)}"
                             f"{(' ' + words[1][1]) if words[1][1] else ''}" if types[1] != "AdministrativeSex" else
                             f"have {obj[0]} but are not {obj[1]}", f"have {obj[0]}, excluding those with {obj[1]}"))
        if types[1] == "AdministrativeSex":
            clause = f"have {obj[0]} but are not {words[1][0]}"
        keep.cue("not", neg)
        return recs, {"and": [{"has": vars_[0]}, {"not": {"has": vars_[1]}}]}, clause, keep, types
    if shape == "before":
        gap = rng.choice(list(GAPS)) if rng.random() < 0.6 else None
        cond = {"first": f"{vars_[0]}.date", "then": f"{vars_[1]}.date"} | ({"gap": gap} if gap else {})
        if gap:
            g = GAPS[gap]
            keep.number(g.split()[0])
            keep.cue("gap unit", g.split()[1].rstrip("s"))
            clause = rng.choice((f"had {obj[0]} at least {g} before {obj[1]}",
                                 f"had {obj[0]} followed at least {g} later by {obj[1]}",
                                 f"had {obj[1]} at least {g} after {obj[0]}"))
        else:
            clause = rng.choice((f"had {obj[0]} before {obj[1]}", f"had {obj[0]} recorded earlier than {obj[1]}",
                                 f"had {obj[1]} after {obj[0]}"))
        keep.cue("order", r"before|prior|earlier|preced|after|followed|later|then|first|subsequent")
        return recs, {"before": cond}, clause, keep, types
    # nested: three records combined two ways
    a, b, c = vars_
    form = rng.choice(("and-or", "or-not", "before-and", "and-not2"))
    if form == "before-and" and not all(P.CURATED[t].get("date") for t in types[:2]):
        form = "and-or"
    keep.cue("logic", r"\band\b|\bor\b|either|both|also|but|not|without|except|before|after")
    if form == "and-or":
        return recs, {"and": [{"has": a}, {"or": [{"has": b}, {"has": c}]}]}, \
            f"have {obj[0]} and also either {obj[1]} or {obj[2]}", keep, types
    if form == "or-not":
        keep.cue("not", neg)
        return recs, {"and": [{"or": [{"has": a}, {"has": b}]}, {"not": {"has": c}}]}, \
            f"have {obj[0]} or {obj[1]}, but not {obj[2]}", keep, types
    if form == "and-not2":
        keep.cue("not", neg)
        return recs, {"and": [{"has": a}, {"not": {"has": b}}, {"not": {"has": c}}]}, \
            f"have {obj[0]} but neither {obj[1]} nor {obj[2]}", keep, types
    keep.cue("order", r"before|prior|earlier|preced|after|followed|later|then|first")
    return recs, {"and": [{"before": {"first": f"{a}.date", "then": f"{b}.date"}}, {"has": c}]}, \
        f"had {obj[0]} before {obj[1]}, and also have {obj[2]}", keep, types


def records_shape(rng, inv, pool, names, unsafe_kind=None):
    """(records, output, question parts, Keep, types) of a one-record-type shape: records counted, first/last dates."""
    dated = [t for t in P.CURATED if P.CURATED[t].get("date") and t != "AdministrativeSex"]
    t = pick_types(rng, inv, 1, exclude=[x for x in P.CURATED if x not in dated])[0]
    v = TYPES[t][0]
    spec, words, keep = record(rng, inv, pool, names, t, v, cond_p=0.3)
    plural = describe(rng, t, words, plural=True)
    single = describe(rng, t, words, article=False)
    form = rng.choice(("records", "records", "records+dates", "records+patients", "first", "last", "first+last"))
    out = {"records": [("records", v)], "records+dates": [("records", v), ("first", v), ("last", v)],
           "records+patients": [("records", v), ("patients", v)], "first": [("first", v)], "last": [("last", v)],
           "first+last": [("first", v), ("last", v)]}[form]
    output = [{"name": {"records": f"{v}_records", "patients": "patients", "first": f"first_{v}", "last": f"last_{v}"}[a],
               "agg": a, **({"of": v} if a == "records" else {"of": f"{v}.date"} if a in ("first", "last") else {})}
              for a, _ in out]
    dv = TYPES[t][3]
    q = {"records": rng.choice((f"How many {plural} are recorded?", f"Count the {plural}.",
                                f"What is the total number of {plural}?")),
         "records+dates": rng.choice((f"How many {plural} are there, and when were the first and the last one {dv}?",
                                      f"Count the {plural} and give the earliest and the latest date.")),
         "records+patients": rng.choice((f"How many {plural} are there, and how many patients do they belong to?",
                                         f"Count the {plural} and the patients who have them.")),
         "first": rng.choice((f"When was the earliest {single} {dv}?", f"What is the date of the first {single}?"))
         if f" {dv} " not in f" {single} " else f"What is the date of the first {single}?",
         "last": rng.choice((f"When was the most recent {single} {dv}?", f"What is the date of the last {single}?"))
         if f" {dv} " not in f" {single} " else f"What is the date of the last {single}?",
         "first+last": f"What are the first and the last dates of {plural}?"}[form]
    if "first" in form or "last" in form or "dates" in form:
        keep.cue("first/last", r"first|earliest|last|latest|most recent|oldest|newest|date|when|range")
    return {v: spec}, output, q, keep, [t], (plural, single)


def sample_safe(rng, inv, pool, names):
    """A safe plan: (plan, question, Keep, meta)."""
    shape = weighted(rng, [("one", 30), ("and", 14), ("or", 10), ("not", 7), ("exclude", 9), ("before", 12),
                           ("nested", 4), ("neither", 2), ("records", 12)])
    if shape == "records":
        recs, output, q, keep, types, _ = records_shape(rng, inv, pool, names)
        return {"route": "plan", "records": recs, "output": output}, q, keep, {"shape": "records", "types": types}
    recs, cohort, clause, keep, types = cohort_records(rng, inv, pool, names, shape)
    plan = {"route": "plan", "records": recs, "cohort": cohort, "output": [{"name": "patients", "agg": "patients"}]}
    return plan, count_question(rng, clause), keep, {"shape": shape, "types": types}


def graph_name(rng):
    n = rng.randint(1000001, 1100000)
    while "1012502" in str(n):
        n = rng.randint(1000001, 1100000)
    m = rng.randint(1, 999)
    while m == 42:
        m = rng.randint(1, 999)
    return weighted(rng, [(f"gen_mock_data-1_{n}", 6), (f"mock_data-1_{m:04d}", 3),
                          (f"http://example.org/graph/gen_mock_data-1_{n}", 1)])


def endpoint(rng):
    host = rng.choice(HOSTS)
    scheme = "http" if re.search(r":\d+$|^\d|localhost|internal", host) else rng.choice(("https", "https", "http"))
    return f"{scheme}://{host}{rng.choice(PATHS)}"


def identifier(rng):
    return rng.choice((f"ID-10{rng.randint(10000000, 99999999)}{rng.randint(10000, 99999)}",
                       f"CASE-{rng.randint(2012, 2024)}-{rng.randint(1, 99999):05d}", f"AC-{rng.randint(10**7, 10**8 - 1)}",
                       f"FID-{rng.randint(1, 9999999):07d}", f"ADM{rng.randint(10**5, 10**6 - 1)}",
                       "-".join(f"{rng.getrandbits(b * 4):0{b}x}" for b in (8, 4, 4, 4, 12)),
                       f"LAB-{rng.randint(2015, 2024)}-{rng.randint(1000, 99999)}", f"{rng.randint(10**9, 10**10 - 1)}"))


def sample_unsafe(rng, inv, pool, names):
    """A plan with unsafe parts kept literal: (plan, question, Keep, meta)."""
    kind = weighted(rng, UNSAFE_KINDS)
    meta = {"unsafe": [kind]}
    if kind == "whole":
        return whole_plan(rng)
    if kind == "identifier":
        return identifier_plan(rng, inv, pool, names, meta)
    if kind in ("per_patient", "rows", "CONSTRUCT", "DELETE", "INSERT") or \
            kind in ("graph", "service", "DESCRIBE") and rng.random() < 0.3:
        return unsafe_records(rng, inv, pool, names, kind, meta)
    if kind in ("service", "DESCRIBE", "ASK") and rng.random() < 0.12:  # every patient
        plan = {"route": "plan", "output": [{"name": "patients", "agg": "patients"}]}
        clause = rng.choice(("are in the database", "are stored", "are recorded in total"))
        recs, keep, types, shape = {}, Keep(), [], "all"
    else:
        shape = weighted(rng, [("one", 40), ("and", 15), ("or", 10), ("not", 5), ("exclude", 10), ("before", 12), ("nested", 3)])
        recs, cohort, clause, keep, types = cohort_records(rng, inv, pool, names, shape)
        plan = {"route": "plan", "records": recs, "cohort": cohort, "output": [{"name": "patients", "agg": "patients"}]}
    meta |= {"shape": shape, "types": types}
    unsafe = {}
    if kind == "graph":
        g = graph_name(rng)
        unsafe["graph"] = g
        keep.exact.add(g)
        q = rng.choice((f"{count_question(rng, clause)} Only look at graph {g}.",
                        f"In patient graph {g}, how many patients {clause}?",
                        f"Restricted to graph {g}: count the patients who {clause}."))
    elif kind == "service":
        url = endpoint(rng)
        unsafe["service"] = url
        keep.exact.add(url)
        q = rng.choice((f"{count_question(rng, clause)} Run the query on the SPARQL endpoint {url}.",
                        f"Query {url}: how many patients {clause}?",
                        f"Using the remote endpoint {url}, count the patients who {clause}."))
    elif kind == "list":
        unsafe["expose"] = "list"
        q = rng.choice((f"How many patients {clause}, and what are their pseudo identifiers?",
                        f"Count the patients who {clause} and list their identifiers.",
                        f"How many patients {clause}? Also tell me which patients they are."))
    elif kind == "rows_expose":
        unsafe["expose"] = "rows"
        q = rng.choice((f"List the pseudo identifiers of the patients who {clause}.",
                        f"Which patients {clause}? Return their identifiers.",
                        f"Give me one row per patient who {clause}, with the patient's identifier."))
    elif kind == "DESCRIBE":
        unsafe["form"] = "DESCRIBE"
        q = rng.choice((f"Describe the patients who {clause}.", f"Give me a full description of every patient who {clause}.",
                        f"DESCRIBE all patients who {clause}."))
    else:  # ASK
        unsafe["form"] = "ASK"
        q = rng.choice((f"Is there at least one patient who {clause}?", f"Are there any patients who {clause}? Answer yes or no."))
    if kind in CUES:
        keep.cue(kind, CUES[kind])
    if kind not in ("graph", "service") and shape != "all" and rng.random() < 0.15:  # a second unsafe part
        extra = rng.choice(("graph", "service"))
        if extra == "graph":
            g = graph_name(rng)
            unsafe["graph"], q = g, q + f" Use only graph {g}."
            keep.exact.add(g)
        else:
            url = endpoint(rng)
            unsafe["service"], q = url, q + f" Send it to the endpoint {url}."
            keep.exact.add(url)
        meta["unsafe"] = meta["unsafe"] + [extra]
    plan["unsafe"] = unsafe
    return plan, q, keep, meta


def whole_plan(rng):
    """The whole graph (every triple, or records per type) of one patient's graph or of another endpoint."""
    what, keep = rng.choice(("triples", "types")), Keep()
    keep.cue(what, CUES[what])
    if rng.random() < 0.6:
        g = graph_name(rng)
        keep.exact.add(g)
        q = rng.choice((f"How many triples are stored in graph {g}?", f"Count every triple in patient graph {g}.")) \
            if what == "triples" else rng.choice((f"How many records of each type does graph {g} hold?",
                                                  f"For graph {g}, count the records per type."))
        return {"route": "plan", "unsafe": {"whole": what, "graph": g}}, q, keep, \
            {"shape": "whole", "types": [], "unsafe": ["whole", "graph"]}
    url = endpoint(rng)
    keep.exact.add(url)
    q = rng.choice((f"How many triples does the endpoint {url} hold?", f"Count all triples at {url}.")) \
        if what == "triples" else rng.choice((f"Count the records of each type at {url}.",
                                              f"How many records per type are there at the endpoint {url}?"))
    return {"route": "plan", "unsafe": {"whole": what, "service": url}}, q, keep, \
        {"shape": "whole", "types": [], "unsafe": ["whole", "service"]}


def identifier_plan(rng, inv, pool, names, meta):
    """A record named by its identifier (one administrative case, one lab test, ...)."""
    t = "AdministrativeCase" if rng.random() < 0.5 else pick_types(rng, inv, 1, exclude=("AdministrativeSex", "LabResult"))[0]
    v = TYPES[t][0]
    spec, words, keep = record(rng, inv, pool, names, t, v, cond_p=0.15)
    ident = identifier(rng)
    spec["identifier"] = ident
    keep.exact.add(ident)
    obj, plural, single = describe(rng, t, words), describe(rng, t, words, plural=True), describe(rng, t, words, article=False)
    if rng.random() < 0.6 or not P.CURATED[t].get("date"):
        plan = {"route": "plan", "records": {v: spec}, "cohort": {"has": v}, "output": [{"name": "patients", "agg": "patients"}]}
        q = rng.choice((f"How many patients have {obj} with the identifier {ident}?",
                        f"Count the patients who have {obj} identified as {ident}.",
                        f"Which number of patients has {obj} whose identifier is {ident}?"))
        return plan, q, keep, meta | {"shape": "one", "types": [t]}
    agg = rng.choice(("records", "first"))
    plan = {"route": "plan", "records": {v: spec},
            "output": [{"name": f"{v}_records", "agg": "records", "of": v} if agg == "records" else
                       {"name": f"first_{v}", "agg": "first", "of": f"{v}.date"}]}
    q = rng.choice((f"How many {plural} have the identifier {ident}?", f"Count the {plural} whose identifier is {ident}.")) \
        if agg == "records" else f"What is the date of the {single} with identifier {ident}?"
    return plan, q, keep, meta | {"shape": "records", "types": [t]}


def unsafe_records(rng, inv, pool, names, kind, meta):
    """Unsafe plans over one record type: per patient, raw rows, CONSTRUCT / DELETE / INSERT, graph, service,
    DESCRIBE of the records."""
    if kind in ("CONSTRUCT", "DELETE", "INSERT"):
        t = pick_types(rng, inv, 1, exclude=("AdministrativeSex",))[0]
        v = TYPES[t][0]
        spec, words, keep = record(rng, inv, pool, names, t, v, cond_p=0.3)
        plural, single = describe(rng, t, words, plural=True), describe(rng, t, words, article=False)
        q = {"CONSTRUCT": (f"Give me all {plural} as an RDF graph.", f"Export every {single} as a graph I can download.",
                           f"Build an RDF graph (CONSTRUCT) with all {plural}."),
             "DELETE": (f"Delete all {plural}.", f"Remove every {single} from the database.",
                        f"Erase the {plural} from all patient graphs."),
             "INSERT": (f"Insert a copy of every {single} into the patients' graphs.",
                        f"Write all {plural} again into the graphs (INSERT).", f"Add each {single} back into its patient graph.")}[kind]
        keep.cue(kind, CUES[kind])
        return {"route": "plan", "records": {v: spec}, "unsafe": {"form": kind}}, rng.choice(q), keep, \
            meta | {"shape": "form", "types": [t]}
    recs, output, q, keep, types, (plural, single) = records_shape(rng, inv, pool, names)
    v, t = next(iter(recs)), types[0]
    plan = {"route": "plan", "records": recs, "output": output}
    meta |= {"shape": "records", "types": types}
    unsafe = {}
    if kind == "per_patient":
        unsafe["per_patient"] = True
        output = [m for m in output if m["agg"] != "patients"] or [{"name": f"{v}_records", "agg": "records", "of": v}]
        plan["output"] = output
        aggs = [m["agg"] for m in output]
        what = " and ".join(x for x in (f"the number of {plural}" if "records" in aggs else "",
                                        f"the date of the first {single}" if "first" in aggs else "",
                                        f"the date of the last {single}" if "last" in aggs else "") if x)
        q = rng.choice((f"For each patient, give {what}.", f"Per patient: {what}.", f"List, patient by patient, {what}."))
        if aggs == ["records"] and rng.random() < 0.5:
            q = f"For each patient, how many {plural} do they have?"
    elif kind == "rows":
        attrs = [a for a in ("date", "value", "unit", "age") if P.CURATED[t].get({"unit": "value"}.get(a, a))
                 and not (a in ("value", "unit") and t in NO_VALUE)]
        chosen = [a for a in attrs if rng.random() < 0.6] or [rng.choice(attrs)]
        if "unit" in chosen and "value" not in chosen:
            chosen.insert(0, "value")
        unsafe["rows"] = [f"{v}.{a}" for a in chosen]
        plan["output"] = [{"name": f"{v}_records", "agg": "records", "of": v}]
        label = {"date": "date", "value": "value", "unit": "unit", "age": "patient's age"}
        attrs_txt = " and ".join(label[a] for a in chosen)
        q = rng.choice((f"List the {attrs_txt} of every {single}.", f"Show each {single} with its {attrs_txt}.",
                        f"Give me one row per {single}: {attrs_txt}."))
        for a in chosen:
            keep.cue(f"row {a}", {"date": r"date|when", "value": r"value|result|measure|reading", "unit": r"unit",
                                  "age": r"\bage|old"}[a])
    elif kind == "graph":
        g = graph_name(rng)
        unsafe["graph"] = g
        keep.exact.add(g)
        q = rng.choice((q[:-1] + f" for the patient in graph {g}" + q[-1], f"In graph {g}: {q[0].lower()}{q[1:]}"))
    elif kind == "service":
        url = endpoint(rng)
        unsafe["service"] = url
        keep.exact.add(url)
        q = rng.choice((f"{q} Use the endpoint {url}.", f"At the SPARQL endpoint {url}: {q[0].lower()}{q[1:]}"))
    else:  # DESCRIBE the records
        unsafe["form"] = "DESCRIBE"
        plan["output"] = [{"name": f"{v}_records", "agg": "records", "of": v}]
        q = rng.choice((f"Describe every {single}.", f"Give me the full description of all {plural}."))
    if kind in CUES:
        keep.cue(kind, CUES[kind])
    plan["unsafe"] = unsafe
    return plan, q, keep, meta


# ---- free requests: what neither v1 nor v2 plans say (two record types together, medians, bins, same stay,
# per-patient sequences, months, record types the plan has not) ----------------------------------------------------

def sample_free(rng, inv, pool, names):
    """A request a plan cannot say: (plan {"route": "free"}, question, Keep, meta)."""
    template, cue, kind = rng.choice(FREE)
    keep = Keep()
    keep.cue(kind, cue)
    slots = {}
    for slot in re.findall(r"\{(\w+)\}", template):
        if slot in ("dx", "dx_short"):
            spec, words, k = record(rng, inv, pool, names, "BilledDiagnosis", "dx", cond_p=0.0, code_p=1.0)
            if any(c.startswith(FREE_AVOID) for c in spec.get("code", [])):
                return sample_free(rng, inv, pool, names)
            slots[slot] = describe(rng, "BilledDiagnosis", words, article=slot == "dx")
            keep = merge([keep, k])
        elif slot in ("rec", "rec2", "rec2_short"):
            t = pick_types(rng, inv, 1, exclude=("AdministrativeSex", "AdministrativeCase", "LabResult"))[0]
            spec, words, k = record(rng, inv, pool, names, t, "r", cond_p=0.0)
            slots[slot] = describe(rng, t, words, article=slot != "rec2_short")
            keep = merge([keep, k])
        elif slot == "rec2_kind":
            slots[slot] = rng.choice(("drug", "lab test", "procedure"))
        elif slot == "plural":
            slots[slot] = TYPES[pick_types(rng, inv, 1, exclude=("AdministrativeSex", "LabResult"))[0]][2]
        elif slot == "meas":
            slots[slot] = MEAS[rng.choice(list(MEAS))]
        else:
            x = {"n": rng.choice((3, 5, 10)), "days": rng.choice((7, 14, 30, 90)), "age": rng.choice((50, 65, 80)),
                 "year": rng.randint(2012, 2022), "bin": rng.choice((5, 10))}[slot]
            slots[slot] = x
            keep.number(x)
    q = template.format(**slots)
    if "percentile" in q:
        keep.number(90)
    return {"route": "free"}, q, keep, {"shape": "free", "types": [], "free": kind}
