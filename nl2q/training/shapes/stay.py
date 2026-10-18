"""Training data for the same-stay shape of plan.py: cohort {"same_stay": [a, b]} (both records in one hospital stay),
group key {"stay_has": b} and "if": {"stay_has": b} (the unit's rows split by whether their stay has a record b).
  python -m nl2q.training.shapes.stay --plans 600 --seed 0
Code makes every plan (record types that link to a stay, codes and conditions from the inventory, as training.make)
and a plain template sentence from it; the 27B generator rewrites the template (training.make.reword: the organizers'
voice for half of the plans) and a rewrite counts only when training.make.problems() finds nothing lost or added.
Every plan passes check(), compile() and training.make.verify(). The right plan of O02 (drug administrations in stays
billed for cholera) and its neighbours (DrugAdministrationEvent rows, stay record with ICD-10-GM A00) are never written.
Writes nl2q_data/training/train_stay.jsonl and val_stay.jsonl (5% of the plans), one row per question."""
import argparse, json, random, time
from collections import Counter
from .. import make as M
from ...translation import plan as P

STAY = [t for t in P.STAY_TYPES if t in M.TYPES and M.TYPES[t][4] > 0]
O02 = ("DrugAdministrationEvent", "icd-10-gm:A00")
SAME = ("in the same hospital stay", "during the same hospital stay", "within one hospital stay", "during the same admission",
        "in a single hospital stay")
SAME_CUE = r"same|single|one (hospital )?(stay|admission|visit)|during (a|the|that|one) (hospital )?(stay|admission|visit)"
STAY_CUE = r"stay|admission|admitted|hospitali[sz]|visit|encounter|episode|case"
OTHER_CUE = r"other|rest|remaining|versus|vs\.?|compar|without|no |not|split|by whether|whether|differ|against"
MEASURE = {"avg": ("the average {x}", "the mean {x}"), "min": ("the shortest {x}", "the lowest {x}"),
           "max": ("the longest {x}", "the highest {x}"), "sum": ("the total {x}",)}
AGG_CUE = {"avg": r"average|mean|typical", "min": r"min|short|low|small|least", "max": r"max|long|high|large|most",
           "sum": r"total|sum|overall|altogether|combined", "share": r"share|percent|%|proportion|fraction|rate|how many of",
           "patients": r"patient|people|individual|person", "records": r"how many|number|count|total"}


def pick(rng, inv, exclude=()):
    """A record type that links to a stay, weighted toward those with data."""
    items = [(t, M.TYPES[t][4] * (1.0 if inv[t]["patients"] is None or inv[t]["patients"] > 0 else 0.1))
             for t in STAY if t not in exclude]
    return M.weighted(rng, items)


def rec(rng, inv, pool, t, name, code_p=None, cond_p=0.3):
    """(record spec, noun phrase, Keep) of one record, as training.make.record makes it."""
    spec, words, keep = M.record(rng, inv, pool, {}, t, name, cond_p=cond_p, code_p=code_p)
    return spec, M.describe(rng, t, words), M.describe(rng, t, words, plural=True), keep


def two_names(t1, t2):
    a, b = M.names_for([t1, t2])
    return a, b


def count_plan(rng, inv, pool):
    """Patients with two records in one stay, alone or with and / or / not."""
    t1 = pick(rng, inv)
    t2 = pick(rng, inv, exclude=() if rng.random() < 0.15 else (t1,))
    a, b = two_names(t1, t2)
    ra, da, _, ka = rec(rng, inv, pool, t1, a, code_p=0.85)
    rb, db, _, kb = rec(rng, inv, pool, t2, b, code_p=0.85)
    recs, keeps, same = {a: ra, b: rb}, [ka, kb], rng.choice(SAME)
    form = rng.random()
    if form < 0.5:
        cohort, text = {"same_stay": [a, b]}, f"How many patients had {da} and {db} {same}?"
    elif form < 0.85:
        t3 = pick(rng, inv, exclude=(t1, t2))
        c = M.names_for([t1, t2, t3])[2]
        rc, dc, _, kc = rec(rng, inv, pool, t3, c, code_p=0.8)
        recs[c] = rc
        keeps.append(kc)
        if rng.random() < 0.5:
            cohort, text = {"and": [{"same_stay": [a, b]}, {"has": c}]}, \
                f"How many patients had {da} and {db} {same}, and also had {dc}?"
        else:
            cohort, text = {"and": [{"same_stay": [a, b]}, {"not": {"has": c}}]}, \
                f"How many patients had {da} and {db} {same}, but never had {dc}?"
            kc.cue("not", r"never|not|no |without|exclud|except|but")
        if rng.random() < 0.3:
            cohort, text = {"or": [{"same_stay": [a, b]}, {"same_stay": [a, c]}]}, \
                f"How many patients had {da} {same} as either {db} or {dc}?"
            kc.cue("or", r"\bor\b|either|any of")
    else:
        cohort, text = {"and": [{"has": a}, {"not": {"same_stay": [a, b]}}]}, \
            f"How many patients had {da} but never had {db} {same} as it?"
        ka.cue("not", r"never|not|no |without|but|separate|different")
    keep = M.merge(keeps)
    keep.cue("same stay", SAME_CUE)
    keep.cue("stay", STAY_CUE)
    return {"route": "plan", "records": recs, "cohort": cohort, "output": [{"name": "patients", "agg": "patients"}]}, text, keep


def threshold(rng, t, name, keep):
    """({"attr", "op", "value"}, phrase) of a share's condition on a unit row, or None."""
    nums = M.v2_attrs(t)
    if not nums:
        return None
    a = rng.choice(nums)
    if a == "hours":
        v, op = rng.choice((6, 12, 24, 48, 72, 96, 168)), rng.choice(("<=", ">"))
        phrase = f"lasting {v} hours or less" if op == "<=" else f"lasting more than {v} hours"
    elif a == "days":
        v, op = rng.choice((1, 2, 3, 5, 7, 10, 14, 30)), rng.choice(("<=", ">"))
        phrase = f"lasting {v} days or less" if op == "<=" else f"lasting more than {v} days"
    elif a == "age":
        v, op = rng.choice(M.AGES), rng.choice((">=", "<"))
        phrase = f"at age {v} or older" if op == ">=" else f"at an age under {v}"
    else:
        return None
    keep.number(v)
    keep.cue(f"{a} {op}", {"<=": r"or less|at most|or fewer|up to|no more|within|or shorter|or under|≤|<=",
                           ">": r"more than|over|longer|above|exceed|greater|>|beyond",
                           ">=": r"or older|at least|or more|or above|≥|>=|and older|and over",
                           "<": r"under|younger|below|less than|<"}[op])
    if op in (">", "<"):
        keep.forbid.append(M.INCLUSIVE)
    return {"attr": f"{name}.{a}", "op": op, "value": v}, phrase


def measures(rng, t, u, keep, many=True):
    """([outputs], [phrases]) of row measures on the unit u of type t."""
    out, words = [], []
    nums = M.v2_attrs(t)
    kinds = ["patients", "records"] + (["num"] * 2 if nums else [])
    for k in rng.sample(kinds, min(len(kinds), rng.choice((1, 2, 2, 3)) if many else 1)):
        if k == "patients":
            out.append({"name": "patients", "agg": "patients"})
            words.append("the number of patients")
        elif k == "records":
            out.append({"name": "records", "agg": "records", "of": u})
            words.append(f"the number of {M.TYPES[t][2]}")
        else:
            a = rng.choice(nums)
            for agg in rng.sample(("avg", "min", "max"), rng.choice((1, 1, 2))):
                x = {"hours": "duration in hours", "days": "duration in days", "age": "patient age",
                     "value": M.VNOUN.get(t, "value")}[a]
                m = {"name": f"{agg}_{a}", "agg": agg, "of": f"{u}.{a}"}
                if agg == "avg" and rng.random() < 0.4:
                    m["round"] = True
                    x += " (rounded)"
                    keep.cue("rounded", r"round|whole|nearest|integer")
                out.append(m)
                words.append(rng.choice(MEASURE[agg]).format(x=x))
                keep.cue(agg, AGG_CUE[agg])
    names = [m["name"] for m in out]
    if len(set(names)) != len(names):
        return None
    return out, words


def extra_group(rng, t, u, keep):
    """An optional second group key (sex, year, the unit's code) with its phrase, or ([], "")."""
    x = rng.random()
    if x < 0.15:
        keep.cue("sex", r"sex|gender|male|female|men|women")
        return [{"sex": True}], " by sex"
    if x < 0.25 and P.CURATED[t].get("date"):
        keep.cue("year", r"year|annual|per year|each year|yearly")
        return [{"year": f"{u}.date"}], " per year"
    return [], ""


def join(words):
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def group_plan(rng, inv, pool):
    """The unit's rows split by whether their stay has a record b (group stay_has)."""
    t = pick(rng, inv)
    t2 = pick(rng, inv, exclude=() if rng.random() < 0.15 else (t,))
    u, b = two_names(t, t2)
    ru, du, pu, ku = rec(rng, inv, pool, t, u, code_p=0.45, cond_p=0.2)
    rb, db, _, kb = rec(rng, inv, pool, t2, b, code_p=0.95)
    keep = M.merge([ku, kb])
    got = measures(rng, t, u, keep)
    if got is None:
        return None
    out, words = got
    if rng.random() < 0.35 and (th := threshold(rng, t, u, keep)):
        test, phrase = th
        out.append({"name": "share", "agg": "share", "if": test})
        words.append(f"the share of them {phrase}")
        keep.cue("share", AGG_CUE["share"])
    more, by = extra_group(rng, t, u, keep)
    keep.cue("stay", STAY_CUE)
    keep.cue("other stays", OTHER_CUE)
    lead = rng.choice((f"For {pu}, compare hospital stays with {db} against other stays{by}: ",
                       f"Split {pu} by whether their hospital stay also had {db}{by} and give ",
                       f"Comparing stays that had {db} with all other stays{by}, what are "))
    end = "." if not lead.startswith("Comparing") else "?"
    plan = {"route": "plan", "records": {u: ru, b: rb}, "group": [{"stay_has": b}, *more], "output": out}
    return plan, lead + join(words) + end, keep


def share_plan(rng, inv, pool):
    """The share (or patients) of the unit's rows whose stay has a record b (if stay_has)."""
    t = pick(rng, inv)
    t2 = pick(rng, inv, exclude=() if rng.random() < 0.15 else (t,))
    u, b = two_names(t, t2)
    ru, du, pu, ku = rec(rng, inv, pool, t, u, code_p=0.6, cond_p=0.2)
    rb, db, _, kb = rec(rng, inv, pool, t2, b, code_p=0.95)
    keep = M.merge([ku, kb])
    out, words = [], []
    if rng.random() < 0.75:
        out.append({"name": "share", "agg": "share", "if": {"stay_has": b}})
        words.append(f"the share of {pu} that took place in a hospital stay that also had {db}")
        keep.cue("share", AGG_CUE["share"])
    if not out or rng.random() < 0.5:
        out.append({"name": "patients_in_stay", "agg": "patients_if", "if": {"stay_has": b}})
        words.append(f"the number of patients with {du} during a hospital stay that also had {db}")
        keep.cue("patients", AGG_CUE["patients"])
    if rng.random() < 0.4:
        out.insert(0, {"name": "records", "agg": "records", "of": u})
        words.insert(0, f"the number of {pu}")
    group, by = [], ""
    if rng.random() < 0.35:
        group, by = extra_group(rng, t, u, keep)
    keep.cue("stay", STAY_CUE)
    plan = {"route": "plan", "records": {u: ru, b: rb}, "output": out}
    if group:
        plan["group"] = group
    return plan, rng.choice(("What are ", "Give ", "Please report ")) + join(words) + by + "?", keep


def near_o02(plan):
    """The O02 neighbourhood: DrugAdministrationEvent rows tested for a stay with ICD-10-GM A00 (or equal to it)."""
    recs = plan["records"]
    if not P._stays(plan):
        return False
    unit = P.units(plan)
    return any(recs[u]["type"] == O02[0] for u in unit) and \
        any(c.startswith(O02[1]) for b in P._stays(plan) for c in recs[b].get("code") or [])


def sample(n, seed, inv, pool):
    rng, items, seen = random.Random(seed), [], set()
    makers = ((count_plan, 0.35), (group_plan, 0.40), (share_plan, 0.25))
    tries = 0
    while len(items) < n and tries < n * 30:
        tries += 1
        made = M.weighted(rng, makers)(rng, inv, pool)
        if made is None:
            continue
        plan, text, keep = made
        key = P._canonical(plan)
        if key in seen or near_o02(plan):
            continue
        item = {"id": f"s{len(items):05d}", "kind": "safe", "plan": plan, "template": text, "keep": keep.dump()}
        if M.verify(item):
            continue
        seen.add(key)
        items.append(item)
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plans", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true", help="templates only (no rewrites)")
    args = ap.parse_args()
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.0f} s] {m}", flush=True)
    M.CACHE.mkdir(parents=True, exist_ok=True)
    inv, source = M.inventory()
    pool = M.code_pool(inv)
    pool = M.code_pool(inv, M.known_codes(pool, M.Cache(M.CACHE / "known_codes.jsonl")))
    items = sample(args.plans, args.seed, inv, pool)
    log(f"codes from {source}; {len(items)} plans: " + ", ".join(f"{k} {v}" for k, v in Counter(
        "same_stay" if it["plan"].get("cohort") else "group" if any("stay_has" in d for d in it["plan"].get("group") or [])
        else "if" for it in items).items()))
    rewrites = {}
    if not args.no_llm:
        M.wake()
        cache = M.Cache(M.CACHE / "rewrites.jsonl")
        half = len(items) // 2
        for part, pairs in ((items[:half], None), (items[half:], M.PAIRS_ORGANIZERS)):  # the organizers' voice second
            got, retried = M.reword_items(part, cache, pairs)
            rewrites |= got
            log(f"reworded {len(part)} plans ({retried} retried)")
    rng = random.Random(args.seed + 1)
    rows, why = {"train": [], "val": []}, Counter()
    val = set(rng.sample([it["id"] for it in items], max(1, round(len(items) * M.VAL_SHARE))))
    for it in items:
        good = []
        for style, text in rewrites.get(it["id"], []):
            bad = M.problems(text, it)
            why.update(b.split(":")[0] for b in bad)
            if not bad and text not in good:
                good.append(text)
        questions = good[:2] + ([it["template"]] if not good or rng.random() < M.TEMPLATE_SHARE else [])
        rows["val" if it["id"] in val else "train"] += [M.row(q, it["plan"]) for q in questions]
    for split, lines in rows.items():
        path = M.OUT / f"{split}_stay.jsonl"
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))
        log(f"{path}: {len(lines)} questions")
    log(f"rewrites rejected for: {dict(why.most_common(8))}")


if __name__ == "__main__":
    main()
