"""Training data for the plan writer's compare condition (translation/plan.py, cohort {"compare": {"left": "a.attr",
"op": "=" | "!=", "right": "b.attr"}}): code makes every plan and its template sentence, the generator only rewords
the sentence (training.make.reword / problems; half of the plans in the organizers' voice, training.make.PAIRS_ORGANIZERS).
  python -m nl2q.training.shapes.compare --plans 600 --seed 0 [--no-llm]
Plans compare one kind on both sides: birth country and nationality, the codes of two diagnoses or two procedures
(one code system), the dates of two records (by the day), the values of two measurements or lab tests. Every plan
passes check() and compile() and its query parses; codes come from the 5k-patient inventory (training.make.inventory,
code_pool); the right plan of the organizers' example 4 is never written, nor a question like a benchmark one.
Writes nl2q_data/training/train_compare.jsonl and val_compare.jsonl (5% of the plans): one row per question,
{"messages": [system plan.TRAINED, the question, the compact plan]}."""
import argparse, json, random, time
from .. import make as M
from ...translation import plan as P
from ...translation.prompt import finish

# The organizers' example 4 ("born in a country they aren't a citizen of"): its right plan stays out of training.
O04 = {"route": "plan", "records": {"birth": {"type": "Birth"}, "nat": {"type": "Nationality"}},
       "cohort": {"and": [{"has": "nat"}, {"compare": {"left": "birth.country", "op": "!=", "right": "nat.country"}}]},
       "output": [{"name": "patients", "agg": "patients"}]}
CODE_PAIRS = [(("BilledDiagnosis", "NursingDiagnosis"), 3), (("BilledDiagnosis", "ProblemCondition"), 3),
              (("BilledDiagnosis", "OncologyDiagnosis"), 2), (("ProblemCondition", "NursingDiagnosis"), 1),
              (("BilledDiagnosis", "Death"), 2), (("BilledProcedure", "MedicalProcedure"), 3),
              (("MedicalProcedure", "OncologySurgery"), 1), (("BilledProcedure", "RadiotherapyProcedure"), 1),
              (("OncologyDiagnosis", "ProblemCondition"), 1)]
DATED = [("BilledDiagnosis", 4), ("BilledProcedure", 3), ("LabTestEvent", 3), ("DrugAdministrationEvent", 2),
         ("MedicalProcedure", 1), ("ProblemCondition", 1), ("Allergy", 1), ("OncologyDiagnosis", 1)]
VALUED = [("BodyWeightMeasurement", 3), ("HeartRateMeasurement", 2), ("BodyTemperatureMeasurement", 2),
          ("LabTestEvent", 3), ("BodyMassIndex", 1), ("RespiratoryRateMeasurement", 1), ("OxygenSaturationMeasurement", 1)]
NOT_CUE = r"\bnot\b|n't|\bnone\b|\bno\b|never|differ|other than|outside|foreign|abroad|without|any of|other"
SHAPES = [("country", 3), ("code", 4), ("date", 2), ("value", 1)]


def _side(rng, inv, pool, names, rtype, name, code_p, cond_p=0.25):
    """(record spec, words, Keep) of one compared record: codes from the data, sometimes an age or value condition."""
    spec, words, keep = M.record(rng, inv, pool, names, rtype, name, cond_p=cond_p, allow=("code", "age"), code_p=code_p)
    return spec, M.describe(rng, rtype, words), keep


def country(rng, inv, pool, names):
    op = "!=" if rng.random() < 0.6 else "="
    recs = {"birth": {"type": "Birth"}, "nat": {"type": "Nationality"}}
    cmp = {"compare": {"left": "birth.country", "op": op, "right": "nat.country"}}
    keep, extra, where = M.Keep(), "", None
    keep.cue("country", r"countr|born|birth|nationalit|citizen")
    if op == "!=":
        keep.cue("not", NOT_CUE)
    else:
        keep.forbid.append(r"\bnot\b|n't|\bnone\b|differ|abroad|foreign")
    with_nat = rng.random() < 0.5
    parts = [{"has": "nat"}] if with_nat else []
    if rng.random() < 0.4:  # among the patients with another record
        t = M.weighted(rng, [("BilledDiagnosis", 4), ("BilledProcedure", 2), ("LabTestEvent", 1), ("DrugAdministrationEvent", 1)])
        name = M.TYPES[t][0]
        spec, words, k = _side(rng, inv, pool, names, t, name, code_p=0.9)
        recs[name], keep, extra = spec, M.merge([keep, k]), words
        parts.append({"has": name})
    cohort = {"and": parts + [cmp]} if parts else cmp
    if op == "!=":
        core = rng.choice(["were born in a country of which they hold no recorded nationality",
                           "have a birth country that is none of their recorded nationalities",
                           "were born outside every country they are recorded as a citizen of"])
        if with_nat:
            core = rng.choice(["have a recorded nationality and were born in a country that is not one of their nationalities",
                               "have at least one recorded nationality, none of which is their country of birth"])
    else:
        core = rng.choice(["were born in a country of which they hold a recorded nationality",
                           "have a nationality that is also their country of birth",
                           "are recorded as citizens of the country they were born in"])
        if with_nat:
            core = rng.choice(["have a recorded nationality and are citizens of their country of birth",
                               "have a nationality matching their birth country"])
    q = f"How many patients {core}" + (f", among those with {extra}" if extra else "") + "?"
    return {"route": "plan", "records": recs, "cohort": cohort, "output": [{"name": "patients", "agg": "patients"}]}, q, keep


def code(rng, inv, pool, names):
    a_t, b_t = M.weighted(rng, CODE_PAIRS)
    if rng.random() < 0.5:
        a_t, b_t = b_t, a_t
    a, b = M.names_for([a_t, b_t])
    a_spec, a_words, ka = _side(rng, inv, pool, names, a_t, a, code_p=0.7)
    b_spec, b_words, kb = _side(rng, inv, pool, names, b_t, b, code_p=0.15, cond_p=0.1)
    op = "!=" if rng.random() < 0.45 else "="
    keep = M.merge([ka, kb])
    keep.cue("code", r"\bcodes?\b|coded|same|identical|match|also|appear|shared")
    b_plural = M.noun(rng, b_t, plural=True)
    if op == "=":
        core = rng.choice([f"{a_words} whose code is also the code of {b_words}",
                           f"{a_words} coded exactly like {b_words}",
                           f"{a_words} and {b_words} with the identical code"])
        keep.forbid.append(r"\bnot\b|n't|\bnone\b|differ")
    else:
        keep.cue("not", NOT_CUE)
        core = rng.choice([f"{a_words} whose code appears on none of their {b_plural}",
                           f"{a_words} with a code that none of their {b_plural} has",
                           f"{a_words} coded differently from all of their {b_plural}"])
    cmp = {"compare": {"left": f"{a}.code", "op": op, "right": f"{b}.code"}}
    cohort = cmp
    if op == "!=" and rng.random() < 0.3:
        cohort = {"and": [{"has": b}, cmp]}
        core += f", among patients with {M.noun(rng, b_t)}"
    elif op == "=" and rng.random() < 0.2:
        cohort = {"and": [{"has": a}, {"not": cmp}]}
        core = rng.choice([f"{a_words}, but no {M.noun(rng, b_t, article=False)} with any of its codes",
                           f"{a_words} whose code never appears on {b_words}"])
        keep.cue("not", NOT_CUE)
        keep.forbid = []
    q = f"How many patients have {core}?"
    return {"route": "plan", "records": {a: a_spec, b: b_spec}, "cohort": cohort,
            "output": [{"name": "patients", "agg": "patients"}]}, q, keep


def date(rng, inv, pool, names):
    a_t = M.weighted(rng, DATED)
    b_t = M.weighted(rng, [x for x in DATED if x[0] != a_t] if rng.random() < 0.85 else DATED)
    a, b = M.names_for([a_t, b_t])
    a_spec, a_words, ka = _side(rng, inv, pool, names, a_t, a, code_p=0.85)
    b_spec, b_words, kb = _side(rng, inv, pool, names, b_t, b, code_p=0.7, cond_p=0.1)
    op = "!=" if rng.random() < 0.35 else "="
    keep = M.merge([ka, kb])
    keep.cue("date", r"\bdates?\b|\bdays?\b|\bdated\b|\bwhen\b|coincid|on which|same")
    verb = M.TYPES[a_t][3] or "dated"
    other = "another " + M.noun(rng, b_t, article=False) if a_t == b_t else b_words
    if op == "=":
        core = rng.choice([f"{a_words} {verb} on a day on which they also have {other}",
                           f"{a_words} whose date coincides with the date of {other}",
                           f"{a_words} dated the very day of {other}"])
        keep.forbid.append(r"\bnot\b|n't|\bnone\b|differ")
    else:
        keep.cue("not", NOT_CUE)
        plural = M.noun(rng, b_t, plural=True)
        core = rng.choice([f"{a_words} {verb} on a day without any of their {('other ' if a_t == b_t else '') + plural}",
                           f"{a_words} whose date matches none of the dates of their {('other ' if a_t == b_t else '') + plural}"])
    cmp = {"compare": {"left": f"{a}.date", "op": op, "right": f"{b}.date"}}
    q = f"How many patients have {core}?"
    return {"route": "plan", "records": {a: a_spec, b: b_spec}, "cohort": cmp,
            "output": [{"name": "patients", "agg": "patients"}]}, q, keep


def value(rng, inv, pool, names):
    t = M.weighted(rng, VALUED)
    a, b = M.names_for([t, t])
    code_p = 1.0 if t == "LabTestEvent" else 0.0
    a_spec, a_words, ka = _side(rng, inv, pool, names, t, a, code_p=code_p, cond_p=0.0)
    b_spec, b_words, kb = (dict(a_spec), a_words, M.Keep()) if rng.random() < 0.7 else \
        _side(rng, inv, pool, names, t, b, code_p=code_p, cond_p=0.0)
    op = "!=" if rng.random() < 0.3 else "="
    keep = M.merge([ka, kb])
    keep.cue("value", r"value|reading|result|identical|same|equal|exact|repeat")
    if op == "=":
        core = rng.choice([f"{a_words} whose value equals that of {('another one' if b_words == a_words else b_words)}",
                           f"two {M.noun(rng, t, plural=True)} with exactly the same value" if b_words == a_words else
                           f"{a_words} with the very value of {b_words}"])
        keep.forbid.append(r"\bnot\b|n't|\bnone\b|differ")
    else:
        keep.cue("not", NOT_CUE)
        core = f"{a_words} whose value differs from that of every other " + \
            (M.noun(rng, t, article=False) if b_words == a_words else b_words.split(" ", 1)[1])
    cmp = {"compare": {"left": f"{a}.value", "op": op, "right": f"{b}.value"}}
    q = f"How many patients have {core}?"
    return {"route": "plan", "records": {a: a_spec, b: b_spec}, "cohort": cmp,
            "output": [{"name": "patients", "agg": "patients"}]}, q, keep


MAKERS = {"country": country, "code": code, "date": date, "value": value}


def problems(plan):
    """Why a plan cannot be a training answer ([] when it can): check(), compile(), the query parses, not O04's."""
    from ...tools.query_check import valid
    if bad := P.check(plan):
        return bad
    if P._canonical(plan) == P._canonical(O04):
        return ["the right plan of the organizers' example 4"]
    try:
        query = finish(P.compile(plan))
    except P.Unsupported as e:
        return [f"compile: {e}"]
    ok, why = valid(query)
    return [] if ok else [f"parse: {why}"]


def sample(n, seed, inv, pool, names):
    """n items {id, plan, template, keep, shape}: no plan twice."""
    rng, items, seen, tries = random.Random(seed), [], set(), 0
    while len(items) < n and tries < n * 40:
        tries += 1
        shape = M.weighted(rng, SHAPES)
        plan, q, keep = MAKERS[shape](rng, inv, pool, names)
        canon = P._canonical(plan)
        if canon in seen or problems(plan):
            continue
        seen.add(canon)
        items.append({"id": f"c{len(items):05d}", "plan": plan, "template": q, "keep": keep.dump(), "shape": shape})
    return items


def reword_all(items, cache, log):
    """{id: [(style, text)]}: two rewrites per plan (a second try for a plan with fewer than two good ones); the
    second half of the plans in the organizers' voice."""
    out, half = {}, len(items) // 2
    for part, pairs in ((items[:half], None), (items[half:], M.PAIRS_ORGANIZERS)):
        got, retried = M.reword_items(part, cache, pairs)
        out |= got
        log(f"reworded {len(part)} plans, {retried} retried")
    return out


def rows(items, rewrites, held=()):
    """{"train": [...], "val": [...]}: the questions that pass training.make.problems (the template too for ~20% of
    the plans, and for a plan with no good rewrite), none like a benchmark question."""
    from ...translation.prompt import _same
    rng, out = random.Random(1), {"train": [], "val": []}
    for it in items:
        texts, seen = [], set()
        for _, text in rewrites.get(it["id"], []):
            if not M.problems(text, it) and text.lower() not in seen and text != it["template"] and len(texts) < 2:
                seen.add(text.lower())
                texts.append(text)
        if not texts or rng.random() < M.TEMPLATE_SHARE:
            texts.append(it["template"])
        split = "val" if int(M.sha("split", it["id"]), 16) % 100 < M.VAL_SHARE * 100 else "train"
        for text in texts:
            if any(_same(text, h) for h in held):
                continue
            out[split].append(M.row(text, it["plan"]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plans", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true", help="templates only (no rewording)")
    args = ap.parse_args()
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.0f} s] {m}", flush=True)
    M.CACHE.mkdir(parents=True, exist_ok=True)
    inv, source = M.inventory()
    pool = M.code_pool(inv)
    pool = M.code_pool(inv, M.known_codes(pool, M.Cache(M.CACHE / "known_codes.jsonl")))
    names = {}
    if not args.no_llm:
        M.wake()
        icd = sorted({c for t in ("BilledDiagnosis", "OncologyDiagnosis") for c, _ in pool.get(t, [])[:80]})
        names = M.plain_names(icd, M.Cache(M.CACHE / "names.jsonl"))
    items = sample(args.plans, args.seed, inv, pool, names)
    log(f"codes from {source}; {len(items)} plans: " +
        ", ".join(f"{s} {sum(it['shape'] == s for it in items)}" for s in MAKERS))
    rewrites = {} if args.no_llm else reword_all(items, M.Cache(M.CACHE / "rewrites.jsonl"), log)
    out = rows(items, rewrites, M.held_out())
    for split, lines in out.items():
        random.Random(args.seed + 1).shuffle(lines)
        (M.OUT / f"{split}_compare.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines))
    log(f"{M.OUT}: train_compare {len(out['train'])}, val_compare {len(out['val'])} questions from {len(items)} plans")


if __name__ == "__main__":
    main()
