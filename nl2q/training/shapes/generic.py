"""Training data for the generic SPHN 2026.1 record types and details (plan.RECORDS beyond plan.CURATED, plan.GENERIC:
resources/schema_records.json): drug prescriptions, blood pressure readings, body surface areas, tumor specimens, ...
  python -m nl2q.training.shapes.generic --plans 1500 --seed 26 [--no-llm] [--no-db]
1. sample: plans over one generic type, weighted toward the types with more patients in the 5k-patient inventory:
   patients with it or its record count (with a code of the data when it has one), a threshold on a number detail or
   its value (unit stated), an average / lowest / highest (unit stated), a date range or a year, a filter on one coded
   or value-set detail, a breakdown by a detail (top 5 sometimes), by sex or by year, two conditions with a curated or
   another generic type (and / not), and the look-alike pairs: prescribed (DrugPrescription) vs administered
   (DrugAdministrationEvent), a blood pressure reading's pressures (BloodPressure) vs blood pressure measurements
   (BloodPressureMeasurement). Every plan passes check(), type_problems(), cue_mismatch(), training.make.verify(), its
   query passes query_check.valid and, unless --no-db, has a non-empty answer on the 5k-patient Virtuoso.
2. state: a template sentence that names the type in plain words and states every unit.
3. reword: two rewrites by the 27B generator (training.make.reword_items; half of the plans in the organizers' voice),
   kept when problems() finds nothing lost or added and the plan's checks still accept the question.
Writes nl2q_data/training/train_generic.jsonl, val_generic.jsonl (5% of the plans) and stats_generic.md."""
import argparse, json, random, re, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from .. import make as M
from ...translation import plan as P
from ...translation.prompt import finish
from ...tools.query_check import valid

RAW = json.loads(M.INVENTORY.read_text())["record_types"] if M.INVENTORY.exists() else {}
# Left out: types the curated plans already say (an age, a birth's gestational age, a diagnosis, a measurement's
# result value, the insurance of a stay); training them would blur the curated boundary.
SKIP = {"Age", "BirthDate", "DeathDate", "Diagnosis", "Measurement", "GestationalAgeAtBirth", "InsuranceStatus",
        "BodyHeight", "BodyWeight", "BodyTemperature", "HeartRate", "OxygenSaturation", "RespiratoryRate",
        "AssessmentResult", "ReferenceInterpretationResult"}
GEN = [t for t in P.RECORDS if t not in P.CURATED and t not in SKIP]
NOUN = {"BloodPressure": "blood pressure reading", "AccessDevicePresence": "access device record",
        "ImplantPresence": "implant record", "TransplantPresence": "transplant record", "FollowUp": "follow-up record",
        "Biobanksample": "biobank sample", "DataProcessing": "data processing step", "SampleProcessing": "sample processing step",
        "FluidInputOutput": "fluid input or output record", "CivilStatus": "civil status record",
        "VitalStatus": "vital status record", "HomeAddress": "home address record", "Consent": "consent record",
        "BodyPosition": "body position record", "Exposure": "exposure record", "TobaccoExposure": "tobacco exposure record",
        "FluidBalance": "fluid balance record", "CardiacOutput": "cardiac output value", "Circumference": "circumference value",
        "TimeSeriesDataFile": "time series data file", "OrganSupport": "organ support record", "Isolate": "microbial isolate"}
VIA = {"sphn:hasDrug/sphn:hasActiveIngredient/sphn:hasCode": "active ingredient", "sphn:hasLabTest/sphn:hasCode": "lab test",
       "sphn:hasAssessment/sphn:hasCode": "assessment", "sphn:hasAllergen/sphn:hasCode": "allergen",
       "sphn:hasSubstance/sphn:hasCode": "substance"}
UNIT_CUE = {"mmHg": r"mm\s?hg|millimet", "m2": r"m2|m²|square met", "%": r"%|percent", "Cel": r"°\s?c|celsius|degree"}
# Plausible thresholds where the 5k-patient data's quantiles are not (its values are synthetic)
REAL = {("BloodPressure", "systolic_pressure"): (90, 100, 120, 130, 140, 160, 180),
        ("BloodPressure", "diastolic_pressure"): (60, 70, 80, 90, 100, 110), ("BloodPressure", "mean_pressure"): (65, 70, 90, 100, 110),
        ("BodySurfaceArea", "value"): (1.5, 1.7, 1.9, 2, 2.2)}
LOOK = {"DrugPrescription": r"administ", "DrugAdministrationEvent": r"prescri", "BloodPressure": r"measurement",
        "BloodPressureMeasurement": r"systolic|diastolic|mean (arterial )?pressure|reading"}


def words(name):
    return [w.lower() for w in re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|\d+", name)]


def noun(t):
    if t in NOUN:
        return NOUN[t]
    w = [x for x in words(t) if x != "event"]
    return " ".join(w[:-1] if w[-2:] == ["lab", "test"] and len(w) > 2 else w).replace(" lab", "") + \
        (" test" if t.endswith("LabTestEvent") else "")


def plural(phrase):
    head, _, last = phrase.rpartition(" ")
    last = last[:-1] + "ies" if last.endswith("y") and last[-2] not in "aeiou" else last + "es" if last.endswith(("s", "x", "sh")) \
        else last if last.endswith("data") else last + "s"
    return f"{head} {last}".strip()


def article(phrase):
    return ("an " if phrase[0] in "aeiou" else "a ") + phrase


def var(t):
    w = [x for x in words(t) if x not in ("event", "lab", "test", "presence")]
    name = "_".join(w)
    return name if len(name) <= 18 else "_".join(x[:4] for x in w)[:18]


def rec_count(v):
    return {"name": f"{v}_records" if len(v) <= 16 else "records", "agg": "records", "of": v}


def prop_words(prop):
    """'sphn:hasReportDateTime' -> 'report date'."""
    w = words(re.sub(r"^has", "", prop.split("/")[-1].split(":")[-1]))
    return " ".join(w).replace("date time", "date").replace("record date", "record date").strip() or "date"


def detail_words(alias):
    return {"date_time": "test date", "diastolic_pressure": "diastolic pressure", "systolic_pressure": "systolic pressure",
            "mean_pressure": "mean pressure"}.get(alias, alias.replace("_date_time", " date").replace("_code", "")
                                                  .replace("_", " "))


def unit_text(u):
    return M.ucum_text(u)


def simple_unit(u):
    return re.match(r"^[A-Za-z%°/.0-9]{1,8}$", unit_text(u)) and not re.match(r"^\d", unit_text(u))


# ---- the data of each generic type -----------------------------------------------------------------------------------

def facts():
    """{type: {"patients", "codes": [(code, n)], "dates": {property: (lo, hi)}, "pairs": {path: [(unit, p10, median,
    p90, n)]}, "values": {property: [(local, n)]}, "dcodes": {property: [(code, n)]}}} of the generic types with data
    (a type without its own inventory entry through the records that hold it, e.g. BloodPressure in a measurement)."""
    out = {}
    for t in GEN:
        spec, det = P.RECORDS[t], P.GENERIC.get(t, {})
        hosts = [("", RAW[t], RAW[t]["patients"])] if t in RAW else \
            [(p["property_path"] + "/", d, p["patients"]) for h, d in RAW.items() if h != "Measurement"
             for p in d.get("properties_in_data") or [] if f"sphn:{t}" in p.get("object_kinds", [])]
        if not hosts or not sum(n for *_, n in hosts):
            continue
        f = {"patients": sum(n for *_, n in hosts), "codes": Counter(), "dates": {}, "pairs": {}, "values": {}, "dcodes": {}}
        for pre, d, _ in hosts:
            for c in d.get("codes") or []:
                path = c["path"][len(pre):] if c["path"].startswith(pre) else None
                for x in c.get("top_by_patients") or []:
                    code = M._code(x["iri"])
                    if not path or not M._proper(code) or code.split(":")[0] in P.NOT_CODES:
                        continue
                    if path == spec.get("code") and code.split(":")[0] in (spec["systems"] or (code.split(":")[0],)):
                        f["codes"][code] += x.get("patients", 1)
                    for a, (prop, kind, *allowed) in det.items():
                        if kind == "code" and path == prop and (not allowed or not allowed[0] or code.split(":")[0] in allowed[0]):
                            f["dcodes"].setdefault(a, Counter())[code] += x.get("patients", 1)
            for x in d.get("dates") or []:
                if x.get("min") and x["property"].startswith(pre):
                    f["dates"][x["property"][len(pre):]] = (x["min"][:10], x["max"][:10])
            for v in d.get("numeric_values") or []:
                if v["value_path"].startswith(pre):
                    f["pairs"][v["value_path"][len(pre):]] = [(p["unit_prefixed"], p["p10"], p["median"], p["p90"], p["patients"])
                                                             for p in v["pairs"] if p.get("unit_prefixed") and simple_unit(p["unit_prefixed"])]
            for v in d.get("value_sets") or []:
                for a, (prop, kind, *allowed) in det.items():
                    if kind == "value" and v["path"] == pre + prop:
                        vals = [(x["prefixed"].split(":", 1)[1], x["patients"]) for x in v["values"]]
                        f["values"][a] = [(x, n) for x, n in vals if not allowed or not allowed[0] or x in allowed[0]]
        f["codes"] = f["codes"].most_common(60)
        f["dcodes"] = {a: c.most_common(40) for a, c in f["dcodes"].items()}
        out[t] = f
    return out


def numbers_of(t, f):
    """{alias: pairs} of the number details and the value with data ("value" for the record's own quantity)."""
    spec, out = P.RECORDS[t], {}
    if spec.get("value") and f["pairs"].get(spec["value"] + "/sphn:hasValue"):
        out["value"] = f["pairs"][spec["value"] + "/sphn:hasValue"]
    for a, (prop, kind, *_) in P.GENERIC.get(t, {}).items():
        if kind == "number" and f["pairs"].get(prop + "/sphn:hasValue"):
            out[a] = f["pairs"][prop + "/sphn:hasValue"]
    return out


def dates_of(t, f):
    """{alias: (lo, hi)} of the record's date and its date details with data."""
    spec, out = P.RECORDS[t], {}
    if spec.get("date") in f["dates"]:
        out["date"] = f["dates"][spec["date"]]
    for a, (prop, kind, *_) in P.GENERIC.get(t, {}).items():
        if kind == "date" and prop in f["dates"]:
            out[a] = f["dates"][prop]
    return out


def details_of(t, f):
    """{alias: [(value or code, n)]} of the coded and value-set details with data."""
    return {**{a: v for a, v in f["dcodes"].items() if v}, **{a: v for a, v in f["values"].items() if v}}


# ---- plan parts --------------------------------------------------------------------------------------------------------

def show_code(code, keep):
    system, local = code.split(":", 1)
    keep.exact.add(local)
    keep.cue("code system", re.escape(M.SYSTEMS.get(system, system.upper()))[:6])
    return f"{M.SYSTEMS.get(system, system.upper())} code {local}"


def show_value(value, keep):
    parts = words(value)
    if len(parts) < 2 or len(value) < 6:
        keep.cue(value, re.escape(value))
        return value
    keep.cue(value, r"[\s_-]*".join(map(re.escape, parts)))
    return " ".join(parts)


def number_test(rng, t, v, alias, pairs, keep):
    """(tests, words) for a threshold on a number with its unit (a pair of the data, one of its quantiles)."""
    unit, p10, mid, p90, _ = M.weighted(rng, [(p, p[4] ** 0.5 + 1) for p in pairs[:6]])
    op = rng.choice((">", ">", ">=", "<", "<="))
    x = {">": p10, ">=": mid, "<": p90, "<=": mid}[op] if rng.random() < 0.7 else rng.choice((p10, mid, p90))
    n = M._value(round(x) if abs(x) >= 10 else round(x, 1))
    if (t, alias) in REAL and rng.random() < 0.85:
        n = rng.choice(REAL[(t, alias)])
    keep.number(n)
    keep.cue(f"value {op}", M.CUE_OPS[op])
    if op in (">", "<"):
        keep.forbid.append(M.INCLUSIVE)
    shown = unit_text(unit)
    keep.cue(f"unit {shown}", UNIT_CUE.get(shown, re.escape(shown)))
    form = rng.choice(M.OPS[op])
    text = form.format(v=f"{M._num(n)} {shown}") if "{v}" in form else f"{form} {M._num(n)} {shown}"
    unit_attr = f"{v}.unit" if alias == "value" else f"{v}.{alias}_unit"
    return [{"attr": f"{v}.{alias}", "op": op, "value": n}, {"attr": unit_attr, "op": "=", "value": unit}], text


def date_test(rng, v, alias, lohi, keep):
    lo, hi = lohi
    if rng.random() < 0.45 and int(hi[:4]) - int(lo[:4]) >= 3:
        y = int(M._date_in(rng, lo, hi)[:4])
        keep.number(y)
        keep.cue("year", rf"\b{y}\b")
        return [{"attr": f"{v}.{alias}", "op": ">=", "value": f"{y}-01-01"},
                {"attr": f"{v}.{alias}", "op": "<", "value": f"{y + 1}-01-01"}], f"in {y}"
    d1, d2 = sorted({M._date_in(rng, lo, hi), M._date_in(rng, lo, hi)})[0], M._date_in(rng, lo, hi)
    if d2 <= d1:
        op = rng.choice((">=", "<"))
        keep.dates.add(d1)
        keep.cue(f"date {op}", r"on or after|since|from|starting|as of|onwards|or later|beginning" if op == ">="
                 else r"before|prior|earlier than|until|up to|till")
        return [{"attr": f"{v}.{alias}", "op": op, "value": d1}], ("on or after " if op == ">=" else "before ") + d1
    keep.dates |= {d1, d2}
    keep.cue("date range", r"between|from|\bto\b|and")
    return [{"attr": f"{v}.{alias}", "op": ">=", "value": d1}, {"attr": f"{v}.{alias}", "op": "<=", "value": d2}], \
        f"between {d1} and {d2}"


def detail_test(rng, t, v, alias, vals, keep):
    value = M.weighted(rng, [(x, n ** 0.5 + 1) for x, n in vals[:20]])
    op = "!=" if rng.random() < 0.15 else "="
    shown = show_code(value, keep) if ":" in value else show_value(value, keep)
    if op == "!=":
        keep.cue("not", r"other than|not|except|excluding|besides|apart from|different")
    return {"attr": f"{v}.{alias}", "op": op, "value": value}, \
        f"with {'a ' + detail_words(alias) + ' other than' if op == '!=' else detail_words(alias)} {shown}"


class Rec:
    """One generic record of a plan: its spec, its phrase ('a drug prescription with active ingredient SNOMED CT code
    ...'), its plural and what the question must keep."""

    def __init__(self, rng, t, f, keep, code_p=0.6):
        self.t, self.v, self.f, self.spec = t, var(t), f, {"type": t}
        self.one, self.many, extra = noun(t), plural(noun(t)), ""
        keep.cue("type", "|".join(re.escape(w[:5]) for w in words(t) if w not in P.PLAIN_WORDS) or re.escape(noun(t)))
        if t in LOOK:
            keep.forbid.append(LOOK[t])
        if f["codes"] and rng.random() < code_p:
            code = M.weighted(rng, [(c, n ** 0.5 + 1) for c, n in f["codes"]])
            self.spec["code"] = [code]
            via = VIA.get(P.RECORDS[t]["code"], "")
            extra = f" with {via + ' ' if via else ''}{show_code(code, keep)}"
        self.desc, self.descs = article(self.one) + extra, self.many + extra


def make_rec(rng, facts_, t, keep, code_p=0.6):
    return Rec(rng, t, facts_[t], keep, code_p)


def has_q(rng, clause):
    return rng.choice((f"How many patients have {clause}?", f"Count the patients with {clause}.",
                       f"What is the number of patients who have {clause}?"))


# ---- the forms -----------------------------------------------------------------------------------------------------------

def form_has(rng, F, t, keep):
    r = make_rec(rng, F, t, keep, code_p=0.75)
    if rng.random() < 0.35:
        out = [rec_count(r.v)]
        q = rng.choice((f"How many {r.descs} are there?", f"Count the {r.descs} in the database."))
        keep.cue("count", r"how many|number|count|total")
        return {"route": "plan", "records": {r.v: r.spec}, "output": out}, q
    return {"route": "plan", "records": {r.v: r.spec}, "cohort": {"has": r.v},
            "output": [{"name": "patients", "agg": "patients"}]}, has_q(rng, r.desc)


def form_threshold(rng, F, t, keep):
    r = make_rec(rng, F, t, keep, code_p=0.2)
    alias, pairs = rng.choice(sorted(numbers_of(t, r.f).items()))
    tests, text = number_test(rng, t, r.v, alias, pairs, keep)
    r.spec["where"] = tests
    what = f"{r.one} value" if alias == "value" else detail_words(alias)
    if rng.random() < 0.3:
        out = [rec_count(r.v)]
        return {"route": "plan", "records": {r.v: r.spec}, "output": out}, \
            f"How many {r.descs} have {article(what)} {text}?"
    clause = f"{r.desc} with {article(what)} {text}" if alias != "value" else f"{r.desc} with a value {text}"
    return {"route": "plan", "records": {r.v: r.spec}, "cohort": {"has": r.v},
            "output": [{"name": "patients", "agg": "patients"}]}, has_q(rng, clause)


def form_stat(rng, F, t, keep):
    r = make_rec(rng, F, t, keep, code_p=0.15)
    alias, pairs = rng.choice(sorted(numbers_of(t, r.f).items()))
    unit = M.weighted(rng, [(p[0], p[4] ** 0.5 + 1) for p in pairs[:6]])
    r.spec["where"] = [{"attr": f"{r.v}.unit" if alias == "value" else f"{r.v}.{alias}_unit", "op": "=", "value": unit}]
    agg = M.weighted(rng, [("avg", 3), ("min", 1), ("max", 1.5)])
    keep.cue(agg, {"avg": r"average|mean", "min": r"minim|lowest|smallest|least", "max": r"maxim|highest|largest|biggest|greatest"}[agg])
    shown = unit_text(unit)
    keep.cue(f"unit {shown}", UNIT_CUE.get(shown, re.escape(shown)))
    what = r.one if alias == "value" else detail_words(alias)
    out = [{"name": f"{agg}_{alias}"[:24], "agg": agg, "of": f"{r.v}.{alias}", **({"round": True} if agg == "avg" and rng.random() < 0.3 else {})}]
    word = {"avg": rng.choice(("average", "mean")), "min": "lowest", "max": "highest"}[agg]
    q = f"What is the {word} {what} of {r.descs}, in {shown}" if alias != "value" else f"What is the {word} {what}, in {shown}"
    plan = {"route": "plan", "records": {r.v: r.spec}, "output": out}
    if rng.random() < 0.25:
        plan["group"], q = [{"sex": True}], q + ", by administrative sex"
        keep.cue("sex", M.CUE_V2["sex"])
    return plan, q + "?"


def form_date(rng, F, t, keep):
    r = make_rec(rng, F, t, keep, code_p=0.3)
    alias, lohi = rng.choice(sorted(dates_of(t, r.f).items()))
    tests, text = date_test(rng, r.v, alias, lohi, keep)
    r.spec["where"] = tests
    what = prop_words(P.RECORDS[t]["date"]) if alias == "date" else detail_words(alias)
    keep.cue("date", re.escape(what.split()[0][:5]))
    if rng.random() < 0.3:
        return {"route": "plan", "records": {r.v: r.spec}, "output": [rec_count(r.v)]}, \
            f"How many {r.descs} have {article(what)} {text}?"
    return {"route": "plan", "records": {r.v: r.spec}, "cohort": {"has": r.v}, "output": [{"name": "patients", "agg": "patients"}]}, \
        has_q(rng, f"{r.desc} with {article(what)} {text}")


def form_detail(rng, F, t, keep):
    r = make_rec(rng, F, t, keep, code_p=0.15)
    alias, vals = rng.choice(sorted(details_of(t, r.f).items()))
    test, text = detail_test(rng, t, r.v, alias, vals, keep)
    r.spec["where"] = [test]
    if rng.random() < 0.3:
        return {"route": "plan", "records": {r.v: r.spec}, "output": [rec_count(r.v)]}, \
            f"How many {r.descs} are there {text}?"
    return {"route": "plan", "records": {r.v: r.spec}, "cohort": {"has": r.v}, "output": [{"name": "patients", "agg": "patients"}]}, \
        has_q(rng, f"{r.desc} {text}")


def form_group(rng, F, t, keep):
    """A breakdown by a coded detail or the record's own code (top 5 sometimes), by sex or by year."""
    r = make_rec(rng, F, t, keep, code_p=0.0)
    keys = [("detail", a) for a in details_of(t, r.f)] + [("code", None)] * bool(r.f["codes"]) + [("sex", None)] + \
        [("year", None)] * ("date" in dates_of(t, r.f))
    how, alias = rng.choice(keys)
    aggs = rng.choice((["patients"], ["patients"], ["records"], ["patients", "records"]))
    out = [{"name": "patients", "agg": "patients"} if a == "patients" else rec_count(r.v)
           for a in aggs]
    counts = " and ".join({"patients": "the number of patients", "records": f"the number of {r.many}"}[a] for a in aggs)
    keep.cue("by", r"per|by|each|break|for every|group|split|across|distribution|which|what")
    if how == "detail":
        kind = P.GENERIC[t][alias][1]
        allowed = (P.GENERIC[t][alias][2:] or [[]])[0]
        group = {"code_class": f"{r.v}.{alias}", **({"ns": allowed[0]} if kind == "code" and len(allowed) == 1 else {})}
        by = detail_words(alias) + (" code" if kind == "code" else "")
        keep.cue("detail", re.escape(by.split()[0][:5]))
    elif how == "code":
        system = r.f["codes"][0][0].split(":")[0]
        group = {"code_class": r.v, "ns": system}
        by = f"{VIA.get(P.RECORDS[t]['code'], '')} {M.SYSTEMS.get(system, system.upper())} code".strip()
        keep.cue("code system", re.escape(M.SYSTEMS.get(system, system.upper()))[:6])
    elif how == "sex":
        group, by = {"sex": True}, "administrative sex"
        keep.cue("sex", M.CUE_V2["sex"])
    else:
        group, by = {"year": f"{r.v}.date"}, "year"
        keep.cue("year", M.CUE_V2["year"])
    plan = {"route": "plan", "records": {r.v: r.spec}, "group": [group], "output": out}
    if how in ("detail", "code") and rng.random() < 0.35:
        plan["order"], plan["limit"] = [{"by": out[0]["name"], "desc": True}], 5
        keep.number(5)
        keep.cue("top", r"top|most|highest|largest|leading|frequent|common")
        return plan, f"Which 5 values of {by} have the most {aggs[0] if aggs[0] == 'patients' else r.many} among {r.many}, with {counts}?"
    return plan, rng.choice((f"For {r.many}, give {counts} per {by}.", f"How are {r.many} distributed by {by}: {counts}?"))


def curated(rng, inv, pool, t, keep, cond_p=0.15):
    name = M.TYPES[t][0]
    spec, w, k = M.record(rng, inv, pool, {}, t, name, cond_p=cond_p, code_p=0.8)
    keep.__dict__.update(M.merge([keep, k]).__dict__)
    return name, spec, M.describe(rng, t, w)


def form_two(rng, F, t, keep, inv, pool):
    """Patients with a generic record and (not) another one: a curated record most of the time."""
    r = make_rec(rng, F, t, keep, code_p=0.5)
    if rng.random() < 0.7:
        t2 = M.weighted(rng, [(x, M.TYPES[x][4]) for x in M.TYPES if M.TYPES[x][4] and x not in ("AdministrativeSex", "LabResult")])
        b, spec2, desc2 = curated(rng, inv, pool, t2, keep)
    else:
        t2 = pick(rng, F, exclude=(t,))
        r2 = make_rec(rng, F, t2, keep, code_p=0.5)
        b, spec2, desc2 = r2.v, r2.spec, r2.desc
    if b == r.v:
        return None
    if rng.random() < 0.6:
        return {"route": "plan", "records": {r.v: r.spec, b: spec2}, "cohort": {"and": [{"has": r.v}, {"has": b}]},
                "output": [{"name": "patients", "agg": "patients"}]}, has_q(rng, f"{r.desc} and {desc2}")
    keep.cue("not", r"never|not|no |without|but|lack|exclud|except")
    return {"route": "plan", "records": {r.v: r.spec, b: spec2}, "cohort": {"and": [{"has": r.v}, {"not": {"has": b}}]},
            "output": [{"name": "patients", "agg": "patients"}]}, has_q(rng, f"{r.desc} but no {desc2.split(' ', 1)[1]}")


def form_look(rng, F, t, keep, inv, pool):
    """The look-alike pairs, balanced: prescribed vs administered, a blood pressure reading vs a measurement."""
    if t == "DrugPrescription":
        common = [c for c, _ in F[t]["codes"] if c in dict(pool.get("DrugAdministrationEvent", []))]
        side = rng.random()
        if side < 0.25 or not common:
            r = make_rec(rng, F, t, keep, code_p=1.0)
            return {"route": "plan", "records": {r.v: r.spec}, "cohort": {"has": r.v}, "output": [{"name": "patients", "agg": "patients"}]}, \
                rng.choice((f"How many patients were prescribed a drug with active ingredient {show_code(r.spec['code'][0], keep)} (a drug prescription)?",
                            has_q(rng, r.desc)))
        code = rng.choice(common[:40])
        shown = show_code(code, keep)
        if side < 0.8:
            keep.forbid.append(LOOK["DrugAdministrationEvent"])
            keep.cue("type", r"administ")
            return {"route": "plan", "records": {"drug": {"type": "DrugAdministrationEvent", "code": [code]}}, "cohort": {"has": "drug"},
                    "output": [{"name": "patients", "agg": "patients"}]}, \
                f"How many patients were administered a drug with active ingredient {shown} (a drug administration)?"
        keep.cue("type", r"prescri")
        keep.cue("administered", r"administ|given")
        keep.cue("not", r"never|not|no |without|but")
        return {"route": "plan", "records": {"drug_prescription": {"type": t, "code": [code]}, "drug": {"type": "DrugAdministrationEvent", "code": [code]}},
                "cohort": {"and": [{"has": "drug_prescription"}, {"not": {"has": "drug"}}]}, "output": [{"name": "patients", "agg": "patients"}]}, \
            f"How many patients had a drug prescription with active ingredient {shown} but no drug administration with it?"
    if rng.random() < 0.25:  # BloodPressure: a pressure of a reading (its other forms give many more)
        return form_threshold(rng, F, t, keep) if rng.random() < 0.7 else form_stat(rng, F, t, keep)
    keep.forbid.append(LOOK["BloodPressureMeasurement"])
    keep.cue("type", r"measurement")
    name, spec, desc = curated(rng, inv, pool, "BloodPressureMeasurement", keep, cond_p=0.85)  # dated: distinct plans
    if rng.random() < 0.4:
        return {"route": "plan", "records": {name: spec}, "output": [{"name": "bp_records", "agg": "records", "of": name}]}, \
            f"How many blood pressure measurements are there{' ' + desc.split(' ', 4)[4] if len(desc.split(' ')) > 4 else ''}?"
    return {"route": "plan", "records": {name: spec}, "cohort": {"has": name}, "output": [{"name": "patients", "agg": "patients"}]}, has_q(rng, desc)


FORMS = {"has": form_has, "threshold": form_threshold, "stat": form_stat, "date": form_date, "detail": form_detail,
         "group": form_group, "two": form_two, "look": form_look}


def pick(rng, F, exclude=()):
    return M.weighted(rng, [(t, F[t]["patients"] ** 0.5 * (3 if t in ("DrugPrescription", "BloodPressure") else 1))
                            for t in F if t not in exclude])


def sample_one(rng, F, inv, pool):
    t = pick(rng, F)
    f = F[t]
    weights = [("has", 2), ("threshold", 2.5 * bool(numbers_of(t, f))), ("stat", 2 * bool(numbers_of(t, f))),
               ("date", 1.2 * bool(dates_of(t, f))), ("detail", 2.5 * bool(details_of(t, f))), ("group", 2), ("two", 1.5),
               ("look", 8 * (t in ("DrugPrescription", "BloodPressure")))]  # the curated look-alikes as often
    form = M.weighted(rng, [(k, w) for k, w in weights if w])
    keep = M.Keep()
    fn = FORMS[form]
    made = fn(rng, F, t, keep, inv, pool) if form in ("two", "look") else fn(rng, F, t, keep)
    return (made[0], made[1], keep, form, t) if made else None


def static_ok(plan, text):
    """Why a plan or its question is rejected (None when both pass)."""
    if bad := P.check(plan) or P.type_problems(text, plan):
        return bad[0]
    if why := P.cue_mismatch(text, plan) or P.beyond(text):
        return why
    try:
        ok, why = valid(finish(P.compile(plan)))
    except P.Unsupported as e:
        return f"compile: {e}"
    return None if ok else f"query: {why}"


def sample(n, seed, db=True, log=print):
    """n items {id, kind, plan, template, keep, form, type}: valid, distinct, non-empty on the 5k data with db."""
    inv, _ = M.inventory()
    pool = M.code_pool(inv)
    F = facts()
    if db:
        pool = M.code_pool(inv, M.known_codes(pool, M.Cache(M.CACHE / "known_codes.jsonl")))
    rng, items, seen, why = random.Random(seed), [], set(), Counter()
    for _ in range(n * 40):
        if len(items) >= int(n * (1.35 if db else 1)):
            break
        made = sample_one(rng, F, inv, pool)
        if not made:
            continue
        plan, text, keep, form, t = made
        canon = P._canonical(plan)
        if canon in seen:
            continue
        item = {"id": f"g{len(items):05d}", "kind": "safe", "plan": plan, "template": text, "keep": keep.dump(), "form": form, "type": t}
        if bad := static_ok(plan, text) or M.verify(item) or M.problems(text, item):
            why[str(bad)[:50]] += 1
            continue
        seen.add(canon)
        items.append(item)
    log(f"sampled {len(items)}; rejected: {dict(why.most_common(6))}")
    if not db:
        return items[:n]
    cache = M.Cache(M.CACHE / "database.jsonl")
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(lambda it: M.run_db(it["query"], cache), items))
    good = [it for it, r in zip(items, res) if r.get("ok") and not M.empty(r)]
    log(f"database: {len(good)} of {len(items)} non-empty, {sum(not r.get('ok') for r in res)} failed")
    return good[:n]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plans", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--no-llm", action="store_true", help="templates only (no rewording)")
    ap.add_argument("--no-db", action="store_true", help="do not run the plans on the 5k-patient Virtuoso")
    ap.add_argument("--workers", type=int, default=M.WORKERS, help="parallel rewording calls")
    ap.add_argument("--minutes", type=float, default=600, help="no new rewording after this many minutes")
    args = ap.parse_args()
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.0f} s] {m}", flush=True)
    M.CACHE.mkdir(parents=True, exist_ok=True)
    items = sample(args.plans, args.seed, db=not args.no_db, log=log)
    rewrites = {}
    if not args.no_llm:
        M.wake()
        cache, half, deadline = M.Cache(M.CACHE / "rewrites.jsonl"), len(items) // 2, t0 + args.minutes * 60
        chunks = [(items[:half][i:i + 100], None) for i in range(0, half, 100)]
        chunks = [c for pair in zip(chunks, [(items[half:][i:i + 100], M.PAIRS_ORGANIZERS) for i in range(0, half, 100)])
                  for c in pair]  # both voices in turn, so a stop at the deadline keeps both
        for part, pairs in chunks:
            if time.time() > deadline:
                break
            got, retried = M.reword_items(part, cache, pairs, args.workers)
            rewrites |= got
            log(f"reworded {len(rewrites)} plans ({retried} retried in the last {len(part)})")
        items = [it for it in items if it["id"] in rewrites]  # the plans reworded before the deadline
    rng = random.Random(args.seed + 1)
    val = set(rng.sample([it["id"] for it in items], max(1, round(len(items) * M.VAL_SHARE))))
    rows, why, n_rw, kept_rw = {"train": [], "val": []}, Counter(), 0, 0
    for it in items:
        good = []
        for _, text in rewrites.get(it["id"], []):
            n_rw += 1
            bad = M.problems(text, it) or ([static_ok(it["plan"], text)] if static_ok(it["plan"], text) else [])
            why.update(b.split(":")[0] for b in bad)
            if not bad and text not in good and text != it["template"]:
                good.append(text)
        kept_rw += len(good[:2])
        it["questions"] = good[:2] + ([it["template"]] if not good or rng.random() < M.TEMPLATE_SHARE else [])
        rows["val" if it["id"] in val else "train"] += [M.row(q, it["plan"]) for q in it["questions"]]
    M.OUT.mkdir(parents=True, exist_ok=True)
    for split, lines in rows.items():
        (M.OUT / f"{split}_generic.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    table = lambda title, c: [f"## {title}", "", "| | n |", "|---|---|", *[f"| {k} | {v} |" for k, v in c.most_common()], ""]
    stats = ["# Generic types: training data", "", f"`python -m nl2q.training.shapes.generic --plans {args.plans} --seed {args.seed}"
             f"{' --no-llm' * args.no_llm}{' --no-db' * args.no_db}` in {(time.time() - t0) / 60:.0f} min", "",
             f"- plans: {len(items)}; rows: train {len(rows['train'])}, val {len(rows['val'])}",
             f"- rewrites: {kept_rw} kept of {n_rw} ({kept_rw / max(1, n_rw):.0%})", ""]
    stats += table("Plans by form", Counter(it["form"] for it in items)) + table("Plans by type", Counter(it["type"] for it in items))
    stats += table("Rewrite rejections", why)
    (M.OUT / "stats_generic.md").write_text("\n".join(stats))
    log(f"train {len(rows['train'])}, val {len(rows['val'])} rows; rewrites kept {kept_rw} of {n_rw}; rejected for {dict(why.most_common(8))}")


if __name__ == "__main__":
    main()
