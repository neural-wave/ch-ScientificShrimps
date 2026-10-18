"""Training examples of the gap shape (organizers' example 1): the time from a patient's first record of one kind to
the first of other records after it, and which of those records follow it. About 600 plans (gap outputs, after/next
group keys, or both; dated record pairs such as diagnosis -> procedure or lab test -> drug; codes from the data
inventory), a template sentence per plan written from the plan, two rewrites each by the generator (training.make's
reword / problems; half of the plans in the organizers' voice), only the rewrites that pass problems() kept. Every plan
passes check() and compile(); none is the right plan of the organizers' example 1, nor about its three record types.
Writes nl2q_data/training/train_gap.jsonl and val_gap.jsonl (5% of the plans), one row per question.

python -m nl2q.training.shapes.gap [--plans 600] [--seed 0] [--no-llm]"""
import argparse, json, random, time
from ...translation import plan as P
from .. import make as M

# record type -> (variable name, singular, plural, cue a rewrite must keep)
KINDS = {
    "BilledDiagnosis": ("dx", "diagnosis", "diagnoses", r"diagnos"),
    "OncologyDiagnosis": ("onco", "cancer diagnosis", "cancer diagnoses", r"cancer|oncolog|tumou?r|malignan|neoplas"),
    "NursingDiagnosis": ("nursing", "nursing diagnosis", "nursing diagnoses", r"nurs"),
    "ProblemCondition": ("problem", "recorded problem", "recorded problems", r"problem|condition"),
    "LabTestEvent": ("lab", "lab test", "lab tests", r"lab|test"),
    "AdministrativeCase": ("stay", "hospital admission", "hospital admissions", r"admi|hospital|stay"),
    "Allergy": ("allergy", "allergy", "allergies", r"allerg"),
    "AdverseEvent": ("ae", "adverse event", "adverse events", r"adverse|side effect|reaction|event"),
    "BilledProcedure": ("proc", "billed procedure", "billed procedures", r"procedur|operat|intervention"),
    "MedicalProcedure": ("medproc", "medical procedure", "medical procedures", r"procedur|operat|intervention"),
    "OncologySurgery": ("surgery", "cancer surgery", "cancer surgeries", r"surg|operat|resect"),
    "RadiotherapyProcedure": ("rt", "radiotherapy session", "radiotherapy sessions", r"radio|radiation|irradiat"),
    "DrugAdministrationEvent": ("drug", "drug administration", "drug administrations", r"drug|medic|administ|dose|given|treat|therap"),
    "Death": ("death", "death", "deaths", r"death|died|dies|deceased|mortal|passed"),
    "HeartRateMeasurement": ("hr", "heart rate measurement", "heart rate measurements", r"heart|pulse"),
    "BodyTemperatureMeasurement": ("temp", "body temperature measurement", "body temperature measurements", r"temperature|fever"),
    "BodyWeightMeasurement": ("weight", "body weight measurement", "body weight measurements", r"weigh"),
    "OxygenSaturationMeasurement": ("spo2", "oxygen saturation measurement", "oxygen saturation measurements", r"oxygen|saturation|spo2"),
}
# what a pathway starts with, and what may follow it (weights)
STARTS = {"BilledDiagnosis": 5, "OncologyDiagnosis": 4, "LabTestEvent": 3, "AdministrativeCase": 2, "ProblemCondition": 2,
          "NursingDiagnosis": 1, "Allergy": 1, "AdverseEvent": 1, "BilledProcedure": 1, "MedicalProcedure": 1,
          "HeartRateMeasurement": 1, "BodyTemperatureMeasurement": 1}
NEXTS = {"BilledProcedure": 4, "MedicalProcedure": 3, "DrugAdministrationEvent": 5, "LabTestEvent": 3, "OncologySurgery": 2,
         "RadiotherapyProcedure": 2, "Death": 2, "AdverseEvent": 1, "BodyWeightMeasurement": 1, "HeartRateMeasurement": 1,
         "OxygenSaturationMeasurement": 1, "BilledDiagnosis": 1}
# a start's other date: (alias, how the template says it, cue)
OTHER_DATE = {"OncologyDiagnosis": ("incidence_date", "its incidence date (when the cancer was first diagnosed)", r"incidence|first diagnosed|diagnosed"),
              "ProblemCondition": ("onset_date", "its onset", r"onset|began|start")}
STAT = {"avg": (("average", "mean"), r"average|mean|typical|on average"),
        "min": (("shortest",), r"shortest|minimum|quickest|fastest|least|soonest|earliest|smallest"),
        "max": (("longest",), r"longest|maximum|slowest|most|largest|greatest")}
UNIT = {"days": r"\bdays?\b|daily", "hours": r"\bhours?\b|hourly"}
O01_TYPES = {"OncologyDiagnosis", "OncologySurgery", "RadiotherapyProcedure"}


def weighted(rng, table, exclude=()):
    items = [(k, w) for k, w in table.items() if k not in exclude]
    return rng.choices([k for k, _ in items], [w for _, w in items])[0]


def a_(word):
    return ("an " if word[0] in "aeiou" else "a ") + word


def join(words, last="or"):
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + f" {last} " + words[-1]


def record(rng, pool, rtype, keep, code_p=0.5):
    """({"type", "code"?}, its words) for one record."""
    name, one, _, cue = KINDS[rtype]
    keep.cue(one, cue)
    codes = [c for c, _ in pool.get(rtype, [])[:30]]
    if codes and rng.random() < code_p:
        code = [rng.choice(codes)]
        phrase, _ = M.code_phrase(rng, code, keep, {})
        return {"type": rtype, "code": code}, f"{one} {phrase}"
    return {"type": rtype}, one


def sample_one(rng, pool):
    """(plan, template, keep) of one gap plan."""
    keep = M.Keep()
    shape = rng.choices(("gap", "next", "both"), (35, 30, 35))[0]
    start = weighted(rng, STARTS)
    n_next = rng.choices((1, 2, 3), (35, 50, 15))[0] if shape != "gap" else rng.choices((1, 2), (70, 30))[0]
    types = [start]
    for _ in range(n_next):
        types.append(weighted(rng, NEXTS, exclude=types))
    if set(types) == O01_TYPES or set(types) >= O01_TYPES:  # the organizers' example 1 stays unseen
        return None
    recs, words = {}, {}
    for t in types:
        r, w = record(rng, pool, t, keep, 0.55 if t == start else 0.35)
        recs[KINDS[t][0]] = r
        words[KINDS[t][0]] = w
    a, nexts = KINDS[start][0], [KINDS[t][0] for t in types[1:]]
    alias, date_words = "date", ""
    if start in OTHER_DATE and rng.random() < 0.6:
        alias, date_words, cue = OTHER_DATE[start]
        keep.cue("date", cue)
        date_words = f", counted from {date_words}"
    plan = {"route": "plan", "records": recs}
    out, sentence = [], []
    if shape != "gap":
        plan["group"] = [{"after": a, "next": nexts}]
    if shape == "next" or rng.random() < 0.5:
        out.append({"name": "patients", "agg": "patients"})
    gap = None
    if shape != "next":
        stat = rng.choices(("avg", "min", "max"), (60, 20, 20))[0]
        unit = rng.choices(("days", "hours"), (80, 20))[0]
        to = nexts if shape == "gap" or rng.random() < 0.7 else rng.sample(nexts, rng.randint(1, len(nexts)))
        to = [n for n in nexts if n in to]
        name = {"avg": "avg", "min": "min", "max": "max"}[stat] + f"_{unit}_to_" + ("next" if len(to) > 1 else to[0])
        gap = {"name": name[:24], "agg": "gap", "stat": stat, "from": f"{a}.{alias}", "to": [f"{n}.date" for n in to],
               "in": unit}
        if stat == "avg" and rng.random() < 0.6:
            gap["round"] = True
        out.append(gap)
        keep.cue("statistic", STAT[stat][1])
        keep.cue("unit", UNIT[unit])
    plan["output"] = out
    if shape != "gap" and rng.random() < 0.5:
        plan["order"] = [{"by": "patients", "desc": True}]
    # the template, written from the plan
    later = [KINDS[recs[n]["type"]][1] for n in nexts]
    first = f"first {words[a]}"
    if shape == "gap":
        statw = rng.choice(STAT[gap["stat"]][0])
        what = join([words[n.split('.')[0]] for n in gap["to"]])  # the next records' codes are said here only
        sentence.append(f"For patients with {a_(words[a])}, what is the {statw} time in {gap['in']} from their {first}"
                        f"{date_words} to the first {what} after it" + (", rounded to whole numbers" if gap.get("round") else "") + "?")
        if any(m["agg"] == "patients" for m in out):
            sentence.append(f"Also give the number of patients with {a_(KINDS[start][1])}.")
    else:
        combos = ("both, or neither" if len(later) == 2 else "any combination of them, or none" if len(later) == 3 else "or not")
        nexts_words = [words[n] for n in nexts]
        sentence.append(f"After a patient's {first}{date_words}, which of these follow it: {join(nexts_words, 'or')}, {combos}? "
                        f"Count the patients in each group" + (", the largest group first" if plan.get("order") else "") + ".")
        if gap:
            statw = rng.choice(STAT[gap["stat"]][0])
            what = join([KINDS[recs[n.split('.')[0]]["type"]][1] for n in gap["to"]])
            sentence.append(f"For each group, give the {statw} time in {gap['in']} from the first {KINDS[start][1]}"
                            f" to the first {what} after it" + (", rounded to whole numbers" if gap.get("round") else "") + ".")
        keep.cue("groups", r"both|neither|none|nothing|combination|each|which|group|categor|pathway|follow")
    return plan, " ".join(sentence), keep


def sample(n, seed, pool):
    rng, items, seen = random.Random(seed), [], set()
    o01 = P._canonical(next(json.loads(line)["plan"] for line in (P.HERE / "resources" / "plans.jsonl").read_text().splitlines()
                            if json.loads(line)["id"] == "org-01"))
    tries = 0
    while len(items) < n and tries < n * 50:
        tries += 1
        got = sample_one(rng, pool)
        if got is None:
            continue
        plan, text, keep = got
        canon = P._canonical(plan)
        if canon in seen or canon == o01 or P.check(plan):
            continue
        try:
            P.compile(plan)
        except P.Unsupported:
            continue
        seen.add(canon)
        items.append({"id": f"gap{len(items):05d}", "plan": plan, "template": text, "keep": keep.dump()})
    return items


def reword_all(items, cache):
    """{id: [rewrites that pass problems()]}: the second half of the plans in the organizers' voice."""
    half = len(items) // 2
    got = M.reword_items(items[:half], cache)[0] | M.reword_items(items[half:], cache, M.PAIRS_ORGANIZERS)[0]
    return {it["id"]: list(dict.fromkeys(x for _, x in got[it["id"]] if not M.problems(x, it))) for it in items}


def rows(items, texts):
    return [M.row(q, it["plan"]) for it in items for q in texts.get(it["id"], [])]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plans", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true", help="templates only, as the questions (no rewording)")
    args = ap.parse_args()
    t0 = time.time()
    inv, source = M.inventory()
    M.CACHE.mkdir(parents=True, exist_ok=True)
    pool = M.code_pool(inv)
    pool = M.code_pool(inv, M.known_codes(pool, M.Cache(M.CACHE / "known_codes.jsonl")))
    items = sample(args.plans, args.seed, pool)
    print(f"{len(items)} plans (codes from {source})", flush=True)
    if args.no_llm:
        texts = {it["id"]: [it["template"]] for it in items}
    else:
        M.wake()
        texts = reword_all(items, M.Cache(M.CACHE / "rewrites_gap.jsonl"))
    rng = random.Random(args.seed + 1)
    order = items[:]
    rng.shuffle(order)
    n_val = max(1, round(0.05 * len(order)))
    split = {"val": order[:n_val], "train": order[n_val:]}
    for k, part in split.items():
        lines = rows(part, texts)
        rng.shuffle(lines)
        path = M.OUT / f"{k}_gap.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines))
        print(f"{path}: {len(lines)} questions from {len(part)} plans", flush=True)
    print(f"plans without a kept rewrite: {sum(not texts.get(it['id']) for it in items)}; {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
