"""Training data for the plan attributes read from the SPHN 2026.1 schema (plan.MEASURES and the stay's insurance):
quantities (gestational age at birth, drug duration, radiotherapy fractions and radiation quantity, time to
positivity), dates (incidence, onset, last reaction) and the insurance coverage type of a record's stay.
  python -m nl2q.training.shapes.attrs --plans 600 --seed 0
1. sample: valid plans (check() and compile()) over these attributes: counts with a condition on one, averages and
   extremes, shares and patients meeting a condition (grouped by sex, year, a coded detail or the insurance), counts
   per insurance type, raw rows with the new attributes (unsafe rows). Codes and values come from the 5k-patient
   inventory; plans equal to the organizers' examples 10 and 12 are never written.
2. state: a plain template sentence per plan, written from the plan.
3. reword: the 27B generator rewrites it (training.make.reword; half of the plans in the organizers' voice); only
   rewrites that pass training.make.problems() are kept.
Writes nl2q_data/training/train_attrs.jsonl and val_attrs.jsonl (5% of the plans): {"messages": [system plan.TRAINED,
the question, the compact plan]}."""
import argparse, json, random, re
from concurrent.futures import ThreadPoolExecutor
from .. import make as M
from ...translation import plan as P
from ...translation.prompt import finish

INV_RAW = json.loads(M.INVENTORY.read_text())["record_types"] if M.INVENTORY.exists() else {}
# (record type, alias): (words, thresholds or None for the inventory's quantiles, unit words)
NUMBERS = {("Birth", "gestational_age"): ("gestational age at birth", (154, 168, 182, 196, 210, 224, 238, 245, 252, 259,
                                                                        266, 273, 280, 287, 294), "days"),
           ("DrugAdministrationEvent", "duration"): ("duration", None, None),
           ("RadiotherapyProcedure", "fractions"): ("number of fractions", None, None),
           ("RadiotherapyProcedure", "radiation_quantity"): ("radiation quantity", None, None),
           ("LabResult", "time_to_positivity"): ("time to positivity", None, None)}
DATES = {("OncologyDiagnosis", "incidence_date"): "incidence date", ("ProblemCondition", "onset_date"): "onset date",
         ("Allergy", "last_reaction_date"): "last reaction date"}
INSURED = ("BilledDiagnosis", "BilledProcedure", "LabTestEvent", "DrugAdministrationEvent", "MedicalProcedure",
           "ProblemCondition", "NursingDiagnosis", "OncologyDiagnosis", "AdministrativeCase")
NOUN = {**{t: (v[1][0], v[2]) for t, v in M.TYPES.items()}, "AdministrativeCase": ("a hospital stay", "hospital stays"),
        "Birth": ("a birth", "births"), "LabResult": ("a lab result", "lab results")}
VAR = {**{t: v[0] for t, v in M.TYPES.items()}, "AdministrativeCase": "stay", "Birth": "birth", "LabResult": "result"}
OPWORDS = {">": "above", ">=": "of at least", "<": "below", "<=": "of at most"}
OPCUE = {">": M.CUE_OPS[">"], ">=": M.CUE_OPS[">="], "<": M.CUE_OPS["<"], "<=": M.CUE_OPS["<="]}


def coverage():
    """The insurance coverage types the data holds (value-set individuals, the most frequent first)."""
    for v in (INV_RAW.get("InsuranceStatus") or {}).get("value_sets") or []:
        if v["path"] == "sphn:hasCoverageType":
            return [x["prefixed"].split(":", 1)[1] for x in v["values"]]
    return ["General", "Private", "SemiPrivate", "None", "Other"]


def number_pairs(t, alias):
    """[(unit code, p10, median, p90)] of a quantity attribute in the inventory."""
    path = P.MEASURES[t][alias][0].split("/")[0]
    for v in (INV_RAW.get(t) or {}).get("numeric_values") or []:
        if v["value_path"].startswith(path + "/"):
            return [(p["unit_prefixed"], p["p10"], p["median"], p["p90"]) for p in v["pairs"] if p.get("unit_prefixed")][:12]
    return []


def number_test(rng, t, alias, v, keep):
    """(where tests, words) for a condition on a quantity attribute, with its unit when the data has several."""
    words, fixed, unit_words = NUMBERS[(t, alias)]
    op = rng.choice(list(OPWORDS))
    tests = []
    if fixed:
        n = rng.choice(fixed)
        text = f"a {words} {OPWORDS[op]} {n} {unit_words}"
        if rng.random() < 0.4:
            tests.append({"attr": f"{v}.{alias}_unit", "op": "=", "value": "ucum:d"})
    else:
        pairs = number_pairs(t, alias) or [("ucum:h", 1, 10, 100)]
        unit, lo, mid, hi = rng.choice(pairs)
        n = round(rng.choice((lo, mid, hi)))
        tests.append({"attr": f"{v}.{alias}_unit", "op": "=", "value": unit})
        text = f"a {words} {OPWORDS[op]} {n} (unit UCUM {unit.split(':', 1)[1]})"
        keep.exact.add(unit.split(":", 1)[1])
    keep.number(n)
    keep.cue(op, OPCUE[op])
    keep.cue("attribute", re.escape(words.split()[-1][:6]))
    return [{"attr": f"{v}.{alias}", "op": op, "value": n}] + tests, text


def date_test(rng, t, alias, v, keep):
    words = DATES[(t, alias)]
    year = rng.randint(1995, 2024)
    d = f"{year}-{rng.randint(1, 12):02d}-01"
    op = rng.choice((">=", "<"))
    keep.dates.add(d)
    keep.cue(op, r"after|since|from|on or after|>=|later|starting|onward|or later" if op == ">=" else r"before|prior|earlier|<|until")
    keep.cue("attribute", re.escape(words.split()[0][:5]))
    return [{"attr": f"{v}.{alias}", "op": op, "value": d}], f"an {words} {'on or after' if op == '>=' else 'before'} {d}"


def insurance_test(rng, v, keep, value=None):
    value = value or rng.choice(coverage()[:8])
    keep.cue("insurance", r"insur|coverage|covered|plan")
    keep.cue(value, re.escape(value.lower()) if value != "None" else r"\bnone\b|\bno\b|without|uninsured|not insured")
    return {"attr": f"{v}.insurance", "op": "=", "value": value}, \
        ("no insurance coverage" if value == "None" else f"{value.lower()} insurance coverage" if value != "Other" else "an other insurance coverage")


def record_spec(rng, pool, t, v, keep):
    """The record, with a code from the data for about half of the coded types."""
    spec = {"type": t}
    if "code" in P.CURATED[t] and pool.get(t) and rng.random() < 0.5:
        code = M.weighted(rng, [(c, n ** 0.5 + 1) for c, n in pool[t][:60]])
        if code.startswith("icd-10-gm:A00"):  # organizers' example 12: cholera
            return record_spec(rng, pool, t, v, keep)
        spec["code"] = [code]
        system, local = code.split(":", 1)
        keep.exact.add(local)
        keep.cue("code system", re.escape(M.SYSTEMS.get(system, system))[:6])
        return spec, f"{M.SYSTEMS.get(system, system)} code {local}"
    return spec, None


def described(t, code_words, plural=False):
    one, many = NOUN[t]
    base = many if plural else one
    return f"{base} with {code_words}" if code_words else base


def attribute(rng):
    """(record type, alias, kind) of one new attribute."""
    return M.weighted(rng, [(("Birth", "gestational_age", "number"), 6), (("DrugAdministrationEvent", "duration", "number"), 2),
                            (("RadiotherapyProcedure", "fractions", "number"), 1.5),
                            (("RadiotherapyProcedure", "radiation_quantity", "number"), 1.5),
                            (("LabResult", "time_to_positivity", "number"), 1),
                            (("OncologyDiagnosis", "incidence_date", "date"), 1.5), (("ProblemCondition", "onset_date", "date"), 1.5),
                            (("Allergy", "last_reaction_date", "date"), 1.5),
                            *((((t, "insurance", "value")), 6 / len(INSURED)) for t in INSURED)])


def sample_one(rng, pool):
    """(plan, template, Keep) using one of the new attributes."""
    keep = M.Keep()
    t, alias, kind = attribute(rng)
    v = VAR[t]
    spec, code_words = record_spec(rng, pool, t, v, keep)
    one, many = described(t, code_words), described(t, code_words, plural=True)
    form = M.weighted(rng, [("count", 3), ("avg", 2.5 if kind == "number" else 0), ("share", 2.5), ("group", 2 if kind == "value" else 0),
                            ("rows", 1.5)])
    if kind == "number":
        tests, words = number_test(rng, t, alias, v, keep)
    elif kind == "date":
        tests, words = date_test(rng, t, alias, v, keep)
    else:
        test, words = insurance_test(rng, v, keep)
        tests = [test]
    recs = {v: spec}
    if form == "count":
        spec["where"] = tests
        if rng.random() < 0.3:
            out = [{"name": f"{v}_records", "agg": "records", "of": v}]
            q = f"How many {many} are there with {words}?"
        else:
            out = [{"name": "patients", "agg": "patients"}]
            q = f"How many patients have {one} with {words}?"
        return {"route": "plan", "records": recs, "cohort": {"has": v}, "output": out}, q, keep
    if form == "avg":
        agg = M.weighted(rng, [("avg", 4), ("min", 1), ("max", 1)])
        words_n = NUMBERS[(t, alias)][0]
        out = [{"name": f"{agg}_{alias}"[:24], "agg": agg, "of": f"{v}.{alias}", **({"round": True} if agg == "avg" and rng.random() < 0.5 else {})}]
        unit_tests = [x for x in tests if x["attr"].endswith("_unit")]
        if unit_tests:
            spec["where"] = unit_tests
            unit_words = f" (unit UCUM {unit_tests[0]['value'].split(':', 1)[1]})"
            keep.__dict__.update(M.Keep().__dict__ | {"exact": {x for x in keep.exact if not x.isdigit()} & ({unit_tests[0]['value'].split(':', 1)[1]} | keep.exact)})
        else:
            unit_words = ""
            keep.numbers, keep.cues = set(), [c for c in keep.cues if c[0] in ("code system",)]
        keep.cue(agg, {"avg": r"average|mean", "min": r"minim|lowest|smallest|shortest|earliest", "max": r"maxim|highest|largest|longest|biggest"}[agg])
        word = {"avg": "average", "min": "lowest", "max": "highest"}[agg]
        q = f"What is the {word} {words_n}{unit_words} of {many}"
        g = M.weighted(rng, [(None, 3), ("sex", 1.5), ("insurance", 1.5 if "insurance" in P.extra(t) else 0),
                             ("detail", 1.5 if P.coded(t) and t != "Birth" else 0)])
        group = []
        if g == "sex":
            group, q = [{"sex": True}], q + ", by administrative sex"
            keep.cue("sex", M.CUE_V2["sex"])
        elif g == "insurance":
            group, q = [{"code_class": f"{v}.insurance"}], q + ", per insurance coverage type"
            keep.cue("insurance", r"insur|coverage")
        elif g == "detail":
            a = rng.choice(sorted(P.coded(t)))
            kind_a = P.coded(t)[a][1]
            group, q = [{"code_class": f"{v}.{a}", **({"ns": "snomed"} if kind_a == "code" else {})}], q + f", per {a.replace('_', ' ')}"
            keep.cue("by", r"per|by|each|break|for every|group|split|across")
        plan = {"route": "plan", "records": recs, **({"group": group} if group else {}), "output": out}
        return plan, q + "?", keep
    if form == "share":
        cond = [x for x in tests if not x["attr"].endswith("_unit")][0]
        unit_tests = [x for x in tests if x["attr"].endswith("_unit")]
        if unit_tests:
            spec["where"] = unit_tests
        how = M.weighted(rng, [("share", 1), ("patients_if", 1)])
        if how == "share":
            out = [{"name": f"{alias}_pct"[:24], "agg": "share", "if": cond}]
            q = f"What percentage of {many} have {words}"
            keep.cue("share", M.CUE_V2["share"])
        else:
            out = [{"name": "patients", "agg": "patients"}, {"name": f"patients_{alias}"[:24], "agg": "patients_if", "if": cond}]
            q = f"How many patients have {one}, and how many of them have one with {words}"
        g = M.weighted(rng, [(None, 3), ("sex", 1.5), ("year", 1.5 if P.CURATED[t].get("date") else 0),
                             ("insurance", 1.5 if "insurance" in P.extra(t) and alias != "insurance" else 0)])
        group = []
        if g == "sex":
            group, q = [{"sex": True}], q + ", by administrative sex"
            keep.cue("sex", M.CUE_V2["sex"])
        elif g == "year":
            group, q = [{"year": f"{v}.date"}], q + ", per year"
            keep.cue("year", M.CUE_V2["year"])
        elif g == "insurance":
            group, q = [{"code_class": f"{v}.insurance"}], q + ", per insurance coverage type"
        plan = {"route": "plan", "records": recs, **({"group": group} if group else {}), "output": out}
        return plan, q + "?", keep
    if form == "group":  # counts per insurance coverage type
        keep.cues = [c for c in keep.cues if c[0] == "code system"]
        keep.cue("insurance", r"insur|coverage")
        keep.cue("by", r"per|by|each|break|for every|group|split|across|distribution|which")
        aggs = rng.choice((["patients"], ["patients", "records"], ["records"]))
        out = [{"name": "patients", "agg": "patients"} if a == "patients" else {"name": f"{v}_records", "agg": "records", "of": v} for a in aggs]
        counts = " and ".join({"patients": "the number of patients", "records": f"the number of {NOUN[t][1]}"}[a] for a in aggs)
        plan = {"route": "plan", "records": recs, "group": [{"code_class": f"{v}.insurance"}], "output": out}
        if rng.random() < 0.4:
            plan["order"] = [{"by": out[0]["name"], "desc": True}]
            keep.cue("most", r"most|top|frequent|common|highest|rank|order|sort|descending")
            return plan, f"For {many}, give {counts} per insurance coverage type, the most frequent first.", keep
        return plan, f"For {many}, give {counts} per insurance coverage type.", keep
    # rows (unsafe): each record with its new attribute and its date
    keep.numbers, keep.dates = set(), set()
    keep.cues = [c for c in keep.cues if c[0] == "code system"]
    rows = [f"{v}.{alias}"] if kind != "number" or rng.random() < 0.7 else [f"{v}.{alias}", f"{v}.{alias}_unit"]
    extra_cols = [a for a in ("date", "age") if P.has_alias(P.CURATED[t], a)]
    rows = sorted(set(rows + rng.sample(extra_cols, k=min(len(extra_cols), rng.randint(0, 2)))), key=lambda a: a != f"{v}.{alias}")
    rows = [r if "." in r else f"{v}.{r}" for r in rows]
    names = {"date": "when it was recorded", "age": "the patient's age", "insurance": "the insurance coverage of its stay",
             f"{alias}_unit": "its unit", **({} if alias == "insurance" else {alias: (NUMBERS.get((t, alias)) or (DATES.get((t, alias)),))[0]})}
    cols = ", ".join(names.get(r.split(".")[1], r.split(".")[1]) for r in rows)
    keep.cue("rows", P.CUES["rows"])
    plan = {"route": "plan", "records": recs, "output": [{"name": f"{v}_records", "agg": "records", "of": v}],
            "unsafe": {"rows": rows}}
    return plan, f"Show me each {NOUN[t][0].split(' ', 1)[1]}{' with ' + code_words if code_words else ''}, with {cols}.", keep


def official(plan):
    """True for a plan like the organizers' examples 10 (births by delivery mode with gestational age) and 12 (cholera
    cases with date, age and insurance)."""
    recs = plan.get("records") or {}
    types = {r["type"] for r in recs.values()}
    grouped = {str(d.get("code_class", "")).split(".")[-1] for d in plan.get("group") or []}
    codes = [c for r in recs.values() for c in r.get("code") or []]
    return ("Birth" in types and "delivery_mode" in grouped) or any(c.startswith("icd-10-gm:A00") for c in codes)


def sample(n, seed, pool):
    rng, items, seen, tries = random.Random(seed), [], set(), 0
    while len(items) < n and tries < n * 40:
        tries += 1
        plan, q, keep = sample_one(rng, pool)
        if official(plan) or P.check(plan):
            continue
        canon = P._canonical(plan)
        if canon in seen:
            continue
        try:
            finish(P.compile(plan))
        except P.Unsupported:
            continue
        seen.add(canon)
        item = {"id": f"attrs{len(items):05d}", "kind": "unsafe" if plan.get("unsafe") else "safe", "plan": plan,
                "template": q, "keep": keep.dump()}
        if M.verify(item) is None:
            items.append(item)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    inv, _ = M.inventory()
    pool = M.code_pool(inv)
    items = sample(args.plans, args.seed, pool)
    print(f"{len(items)} plans")
    M.CACHE.mkdir(parents=True, exist_ok=True)
    cache = M.Cache(M.CACHE / "rewrites_attrs.jsonl")
    M.wake()
    half = len(items) // 2
    kept, rejected = {}, 0
    for part, pairs in ((items[:half], None), (items[half:], M.PAIRS_ORGANIZERS)):
        with ThreadPoolExecutor(M.WORKERS) as ex:
            for it, (rw, err) in zip(part, ex.map(lambda it: M.reword(it, cache, 0, pairs), part)):
                good = [text for _, text in rw if not M.problems(text, it)]
                rejected += len(rw) - len(good)
                kept[it["id"]] = good
    rng = random.Random(args.seed)
    val = set(rng.sample([it["id"] for it in items], max(1, round(len(items) * M.VAL_SHARE))))
    rows = {"train": [], "val": []}
    for it in items:
        for text in kept.get(it["id"], []):
            rows["val" if it["id"] in val else "train"].append(M.row(text, it["plan"]))
    M.OUT.mkdir(parents=True, exist_ok=True)
    for split, name in (("train", "train_attrs.jsonl"), ("val", "val_attrs.jsonl")):
        (M.OUT / name).write_text("".join(json.dumps(r) + "\n" for r in rows[split]))
    print(f"train {len(rows['train'])} rows, val {len(rows['val'])} rows, {rejected} rewrites rejected, "
          f"{sum(not v for v in kept.values())} plans without a rewrite")


if __name__ == "__main__":
    main()
