"""Look-alike training pairs for the plan model: questions in pairs of plans that differ in one detail, so the model
learns the detail that tells them apart.
  python -m nl2q.training.shapes.pairs --pairs 190 --seed 0      # --no-llm: the template sentences only
Pair kinds: hours vs days (a duration); a count of patients (patients_if) vs a percent (share); two confusable coded
details of one record type (an adverse event's outcome vs its consequences, a variant's zygosity vs allele origin, an
allergy's severity vs verification status or reaction type); ">" vs ">=" (and "<" vs "<="); "after" vs "on or after"
(and "before" vs "on or before"); "and" vs "or" between two records; counts of lab tests (LabTestEvent) vs of lab
results (LabResult), where the plan language separates them (record counts without a code; see Pairs.lab).
1. plans by code: codes from the 5k-patient inventory (training.make.inventory, code_pool; only codes the loaded
   terminologies have, from the known-codes cache: no database query), coded values from the data that the 2026.1
   schema allows (resources/coded_values.json); every plan passes plan.check() and compile() (training.make.verify);
   no plan equals a worked example's plan (resources/plans.jsonl) or one the benchmark intends.
2. a plain template sentence per plan, with a cue for the distinguishing detail (and the other side's cue forbidden).
3. rewrites by the generator (training.make.reword; half the pairs in the organizers' voice, PAIRS_ORGANIZERS); only
   rewrites that pass training.make.problems() are kept, so each keeps its codes, numbers, dates and the detail.
Writes nl2q_data/training/train_pairs.jsonl and val_pairs.jsonl (5% of the pairs): one row per question, the system
line plan.TRAINED, the question, the compact plan."""
import argparse, json, random, time
from collections import Counter
from ...translation import plan as P
from .. import make as M

OUT = M.OUT
KINDS = ("hours_days", "count_share", "detail", "strict", "after", "and_or", "lab")
SYSCUE = {"icd-10-gm": r"icd", "snomed": r"snomed", "chop": r"chop", "loinc": r"loinc"}
UNIT_WORD = {"hours": (r"\bhours?\b|hourly|\bhrs?\b|\bh\b", r"\bdays?\b|daily"),
             "days": (r"\bdays?\b|daily", r"\bhours?\b|hourly|\bhrs?\b")}
STAT = {"avg": ("average", r"average|mean"), "max": ("longest", r"longest|maximum|\bmax|highest|largest|most"),
        "min": ("shortest", r"shortest|minimum|\bmin|lowest|smallest|least")}
COUNT_CUE, NOT_SHARE = r"how many|number|count", M.CUE_V2["share"]
# confusable coded details of one record type: (type, detail a, detail b, weight, cue of a, cue of b)
CONFUSABLE = [("AdverseEvent", "outcome", "consequences", 3, r"outcome", r"consequence"),
              ("VariantDescriptor", "zygosity", "allele_origin", 1, r"zygo", r"allele origin|origin of the allele"),
              ("Allergy", "severity", "verification_status", 1, r"severity|severe", r"verification|verified"),
              ("Allergy", "severity", "reaction_type", 1, r"severity|severe", r"reaction type|type of reaction")]
OP_WORDS = {">": ("above {v}", "greater than {v}", "over {v}"), ">=": ("of at least {v}", "of {v} or more"),
            "<": ("below {v}", "under {v}", "less than {v}"), "<=": ("of at most {v}", "of {v} or less")}
AGE_WORDS = {">": ("older than {v}",), ">=": ("aged {v} or older", "at age {v} or older"), "<": ("younger than {v}",),
             "<=": ("aged {v} or younger", "at age {v} or younger")}
AGE_CUES = {">": r"older than|over|above|more than|greater|beyond|>", ">=": r"or older|or over|at least|or more|or above|"
            r"and older|and over|≥|>=|no younger|minimum", "<": r"younger than|under|below|less than|<",
            "<=": r"or younger|at most|or less|or under|or below|and younger|≤|<=|no older|up to"}
STRICT = {">", "<"}
DATE_WORDS = {">": "after {d}", ">=": "on or after {d}", "<": "before {d}", "<=": "on or before {d}"}
DATE_CUES = {">": r"\bafter\b|later than|past", ">=": r"on or after|or later|onwards?|\bfrom\b|starting|since|as of|"
             r"beginning|no earlier|inclusive|including", "<": r"\bbefore\b|earlier than|prior to",
             "<=": r"on or before|or earlier|up to|until|through|no later|inclusive|including|\bby\b"}
DATE_FORBID = {">": DATE_CUES[">="], "<": DATE_CUES["<="]}
AND_OR = {"and": (r"\band\b|both|as well|also|plus|together", r"\beither\b|at least one of|\bany of"),
          "or": (r"\bor\b|either|at least one|any of", r"\bboth\b|as well as")}
LAB = {"LabTestEvent": ("lab", "a lab test", "lab tests", r"\btests?\b", r"lab(oratory)? results?\b|result records?"),
       "LabResult": ("result", "a lab result", "lab results", r"\bresults?\b", r"\btests?\b")}


class Pairs:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.inv, _ = M.inventory()
        known = M.Cache(M.CACHE / "known_codes.jsonl").data  # codes not in the cache are left out: no database query
        self.pool = M.code_pool(self.inv, known)
        onco = [(c, n) for c, n in self.pool.get("OncologyDiagnosis", []) if P.CANCER_CODE.match(c)]
        self.pool["OncologyDiagnosis"] = onco  # an oncology diagnosis with a cancer code only (plan.type_problems)
        self.dvals = {}
        for (t, a), vals in M.detail_values(known).items():
            ok = [(v, n) for v, n in vals if a in P.CODED_VALUES.get(t, {}) and P._value_wrong(t, a, v) is None]
            if ok:
                self.dvals[(t, a)] = ok
        self.span = [t for t, s in P.CURATED.items() if P.has_alias(s, "hours") and self.pool.get(t) and t in M.TYPES]
        self.dated = [t for t in ("BilledDiagnosis", "BilledProcedure", "DrugAdministrationEvent", "OncologyDiagnosis",
                                  "RadiotherapyProcedure", "LabTestEvent") if self.pool.get(t)]
        self.loinc = [c for c, _ in self.pool.get("LabTestEvent", []) if c.startswith("loinc:")][:30] or \
            [f"loinc:{c}" for c in M.LOINC]

    def code(self, t, top=60):
        return M.weighted(self.rng, [(c, n ** 0.5 + 1) for c, n in self.pool[t][:top]])

    @staticmethod
    def words(code, keep):
        system, local = code.split(":", 1)
        keep.exact.add(local)
        keep.cue("code system", SYSCUE.get(system, ""))
        return f"{M.SYSTEMS.get(system, system)} code {local}"

    def lab_value(self):
        """(UCUM code, unit as written, threshold): a percent is at most 100."""
        ucum, unit = self.rng.choice(M.LAB_UNITS)
        return ucum, unit, self.rng.choice([x for x in M.LAB_VALUES if ucum != "ucum:percent" or x <= 100])

    def date(self, t):
        lo, hi = self.inv[t].get("dates") or ("2010", "2024")
        y0, y1 = int(lo[:4]), int(hi[:4])
        y = self.rng.randint(min(y0 + 1, y1), max(y0 + 1, y1 - 1)) if y1 > y0 else y0
        return f"{y}-{self.rng.randint(1, 12):02d}-{self.rng.choice((1, 15)):02d}"

    # each kind: [(plan, template, Keep), (plan, template, Keep)] differing in one detail ----------------------------

    def hours_days(self):
        t = self.rng.choice(self.span)
        v, noun, plural = M.TYPES[t][0], M.TYPES[t][1][0], M.TYPES[t][2]
        code, shape, n = self.code(t), self.rng.choice(("stat", "where")), self.rng.choice((2, 3, 4, 5, 6, 7, 8, 10, 12, 14))
        stat = self.rng.choice(list(STAT))
        out = []
        for unit in ("hours", "days"):
            keep = M.Keep()
            cw = self.words(code, keep)
            if shape == "stat":
                plan = {"route": "plan", "records": {v: {"type": t, "code": [code]}},
                        "output": [{"name": f"{stat}_{unit}", "agg": stat, "of": f"{v}.{unit}"}]}
                q = f"What is the {STAT[stat][0]} duration in {unit} of {plural} with {cw}?"
                keep.cue(stat, STAT[stat][1])
            else:
                plan = {"route": "plan", "records": {v: {"type": t, "code": [code],
                                                         "where": [{"attr": f"{v}.{unit}", "op": ">", "value": n}]}},
                        "cohort": {"has": v}, "output": [{"name": "patients", "agg": "patients"}]}
                q = M.count_question(self.rng, f"have {noun} with {cw} that lasted more than {n} {unit}")
                keep.number(n)
                keep.cue(">", M.CUE_OPS[">"])
                keep.forbid.append(M.INCLUSIVE)
            keep.cue(unit, UNIT_WORD[unit][0])
            keep.forbid.append(UNIT_WORD[unit][1])
            out.append((plan, q, keep))
        return out

    def count_share(self):
        keys = [k for k in M.DETAILS if k in self.dvals and k[0] in M.TYPES]
        t, a = self.rng.choice(keys)
        v, noun, plural, seed = M.TYPES[t][0], M.TYPES[t][1][0], M.TYPES[t][2], self.rng.random()
        out = []
        for form in ("patients_if", "share"):
            keep = M.Keep()
            test, with_, have = M.detail_test(random.Random(seed), t, v, a, self.dvals, keep)  # the same value twice
            if form == "patients_if":
                m = {"name": M.nm("patients", a), "agg": "patients_if", "if": test}
                q = f"How many patients have {noun}, and how many of them have one {with_}?"
                keep.cue("count", COUNT_CUE)
                keep.forbid.append(NOT_SHARE)
            else:
                m = {"name": M.nm(a, "pct"), "agg": "share", "if": test}
                q = f"How many patients have {noun}, and what percentage of {plural} {have}?"
                keep.cue("share", M.CUE_V2["share"])
            out.append(({"route": "plan", "records": {v: {"type": t}},
                         "output": [{"name": "patients", "agg": "patients"}, m]}, q, keep))
        return out

    def detail(self):
        t, a, b, _, cue_a, cue_b = M.weighted(self.rng, [(c, c[3]) for c in CONFUSABLE
                                                         if (c[0], c[1]) in self.dvals and (c[0], c[2]) in self.dvals])
        v, noun = M.TYPES[t][0], M.TYPES[t][1][0]
        dvals = dict(self.dvals)
        if t == "AdverseEvent" and self.rng.random() < 0.4:  # the look-alike of O09: a fatal outcome, death as consequence
            dvals[(t, "outcome")], dvals[(t, "consequences")] = [("Fatal", 1)], [("Death", 1)]
        q_shape = self.rng.random()
        out = []
        for alias, cue, other in ((a, cue_a, cue_b), (b, cue_b, cue_a)):
            keep = M.Keep()
            test, with_, _ = M.detail_test(self.rng, t, v, alias, dvals, keep)
            keep.cue(alias, cue)
            keep.forbid.append(other)
            if alias == "consequences" and "consequence" not in with_:
                with_ += " as a consequence"
            if q_shape < 0.7:
                plan = {"route": "plan", "records": {v: {"type": t, "where": [test]}}, "cohort": {"has": v},
                        "output": [{"name": "patients", "agg": "patients"}]}
                q = M.count_question(self.rng, f"have {noun} {with_}")
            else:
                plan = {"route": "plan", "records": {v: {"type": t, "where": [test]}},
                        "output": [{"name": f"{v}_records", "agg": "records", "of": v}]}
                q = f"How many {M.TYPES[t][2]} {with_} are recorded?"
            out.append((plan, q, keep))
        return out

    def strict(self):
        ops = (">", ">=") if self.rng.random() < 0.7 else ("<", "<=")
        kind = M.weighted(self.rng, [("lab", 3), ("measure", 2), ("age", 2)])
        out = []
        if kind == "lab":
            code, (ucum, unit, value), t, v = self.rng.choice(self.loinc), self.lab_value(), "LabTestEvent", "lab"
        elif kind == "measure":
            t = self.rng.choice([x for x in M.VALUES if x not in ("LabTestEvent", "LabResult", "BodyMassIndex")])
            (values, units), v, code = M.VALUES[t], M.TYPES[t][0], None
            value, (ucum, unit) = self.rng.choice(values), units[0]
        else:
            t, v, code = "BilledDiagnosis", "dx", self.code("BilledDiagnosis")
            value = self.rng.choice(M.AGES)
        words_i = self.rng.randrange(3)
        for op in ops:
            keep = M.Keep()
            keep.number(value)
            rec = {"type": t, **({"code": [code]} if code else {})}
            cw = f" with {self.words(code, keep)}" if code else ""
            if kind == "age":
                rec["where"] = [{"attr": f"{v}.age", "op": op, "value": value}]
                phrase = AGE_WORDS[op][words_i % len(AGE_WORDS[op])].format(v=value)
                clause = f"have {M.TYPES[t][1][0]}{cw} recorded when they were {phrase}"
                keep.cue(op, AGE_CUES[op])
            else:
                rec["where"] = [{"attr": f"{v}.unit", "op": "=", "value": ucum}, {"attr": f"{v}.value", "op": op, "value": value}]
                phrase = OP_WORDS[op][words_i % len(OP_WORDS[op])].format(v=value)
                clause = f"have {M.TYPES[t][1][0]}{cw} with a value {phrase} {unit}"
                keep.cue(op, M.CUE_OPS[op])
                if rx := M.UNIT_CUES.get(unit):
                    keep.cue("unit", rx)
            if op in STRICT:
                keep.forbid.append(M.INCLUSIVE)
            plan = {"route": "plan", "records": {v: rec}, "cohort": {"has": v}, "output": [{"name": "patients", "agg": "patients"}]}
            out.append((plan, M.count_question(self.rng, clause), keep))
        return out

    def after(self):
        ops = (">", ">=") if self.rng.random() < 0.7 else ("<", "<=")
        t = self.rng.choice(self.dated)
        v, noun, verb = M.TYPES[t][0], M.TYPES[t][1][0], M.TYPES[t][3]
        code, d = self.code(t), self.date(t)
        out = []
        for op in ops:
            keep = M.Keep()
            keep.dates.add(d)
            cw = self.words(code, keep)
            keep.cue(op, DATE_CUES[op])
            if op in DATE_FORBID:
                keep.forbid.append(DATE_FORBID[op])
            plan = {"route": "plan", "records": {v: {"type": t, "code": [code], "where": [{"attr": f"{v}.date", "op": op, "value": d}]}},
                    "cohort": {"has": v}, "output": [{"name": "patients", "agg": "patients"}]}
            out.append((plan, M.count_question(self.rng, f"have {noun} with {cw} {verb} {DATE_WORDS[op].format(d=d)}"), keep))
        return out

    def and_or(self):
        t1, t2 = self.rng.sample([t for t in ("BilledDiagnosis", "BilledProcedure", "DrugAdministrationEvent", "LabTestEvent",
                                              "OncologyDiagnosis", "Allergy") if self.pool.get(t)], 2)
        (v1, v2), (c1, c2) = (M.TYPES[t1][0], M.TYPES[t2][0]), (self.code(t1), self.code(t2))
        both = self.rng.random() < 0.3
        out = []
        for join in ("and", "or"):
            keep = M.Keep()
            w1, w2 = self.words(c1, keep), self.words(c2, keep)
            keep.cue(join, AND_OR[join][0])
            keep.forbid.append(AND_OR[join][1])
            lead = ("both " if join == "and" else "either ") if both else ""
            plan = {"route": "plan", "records": {v1: {"type": t1, "code": [c1]}, v2: {"type": t2, "code": [c2]}},
                    "cohort": {join: [{"has": v1}, {"has": v2}]}, "output": [{"name": "patients", "agg": "patients"}]}
            q = M.count_question(self.rng, f"have {lead}{M.TYPES[t1][1][0]} with {w1} {join} {M.TYPES[t2][1][0]} with {w2}")
            out.append((plan, q, keep))
        return out

    def lab(self):
        """Record counts only, without a code: there the plan language separates the two (a lab test event can hold
        several results). A patient count or a LOINC code makes a "lab result" a lab test (the worked example
        sphn-lab-value: "a lab result above 15 g/L for LOINC code 718-7" is a LabTestEvent), so those are not pairs."""
        shape = M.weighted(self.rng, [("value", 6), ("sex", 1), ("plain", 1)])
        (ucum, unit, value), op = self.lab_value(), self.rng.choice((">", "<"))
        words_i = self.rng.randrange(3)
        out = []
        for t, (v, _, plural, cue, forbid) in LAB.items():
            keep = M.Keep()
            keep.cue(t, cue)
            keep.forbid.append(forbid)
            keep.cue("count", COUNT_CUE)
            rec, group = {"type": t}, []
            if shape == "value":
                rec["where"] = [{"attr": f"{v}.unit", "op": "=", "value": ucum}, {"attr": f"{v}.value", "op": op, "value": value}]
                keep.number(value)
                keep.cue(op, M.CUE_OPS[op])
                keep.forbid.append(M.INCLUSIVE)
                if rx := M.UNIT_CUES.get(unit):
                    keep.cue("unit", rx)
                phrase = OP_WORDS[op][words_i % len(OP_WORDS[op])].format(v=value)
                q = f"How many {plural} with a value {phrase} {unit} are recorded?"
            elif shape == "sex":
                group = [{"sex": True}]
                keep.cue("sex", M.CUE_V2["sex"])
                q = f"How many {plural} are recorded for each administrative sex?"
            else:
                q = f"How many {plural} are recorded in the database?"
            out.append(({"route": "plan", "records": {v: rec}, **({"group": group} if group else {}),
                         "output": [{"name": f"{v}_records", "agg": "records", "of": v}]}, q, keep))
        return out


def canonical(plan):
    return json.dumps(plan, sort_keys=True, separators=(",", ":"))


def official():
    """The worked examples' plans (resources/plans.jsonl: the official questions' right plans and the others)."""
    return {canonical(json.loads(line)["plan"]) for line in (P.HERE / "resources" / "plans.jsonl").read_text().splitlines()}


def benchmark(plan):
    for f in (M.bench, M.v2_bench, M.v21_bench):
        try:
            if f(plan):
                return True
        except (KeyError, TypeError, AttributeError, IndexError, ValueError):  # not the shape it knows
            pass
    return False


def sample(n, seed):
    """n pairs of items (two plans each), every plan valid and new."""
    gen, banned, seen, pairs, rejected, tries = Pairs(seed), official(), set(), [], Counter(), 0
    while len(pairs) < n and tries < n * 20:
        tries += 1
        kind = KINDS[len(pairs) % len(KINDS)]
        try:
            two = getattr(gen, kind)()
        except (IndexError, ValueError, KeyError) as e:
            rejected[f"{kind}: {type(e).__name__}"] += 1
            continue
        items = []
        for side, (plan, template, keep) in zip("ab", two):
            items.append({"id": f"pair{len(pairs):04d}{side}", "plan": plan, "template": template, "keep": keep.dump(),
                          "kind": "safe", "pair": kind, "meta": {"shape": f"pair {kind}", "types": []}})
        keys = [canonical(it["plan"]) for it in items]
        if keys[0] == keys[1]:
            rejected["same plans"] += 1
        elif any(k in banned for k in keys) or any(benchmark(it["plan"]) for it in items):
            rejected["a worked example's or the benchmark's plan"] += 1
        elif any(k in seen for k in keys):
            rejected["plan already sampled"] += 1
        elif why := next((w for it in items if (w := M.verify(it))), None):
            rejected[why[:70]] += 1
        else:
            seen.update(keys)
            pairs.append(items)
    return pairs, rejected


def questions(item, rewrites, rng, held):
    """The item's questions: up to two good rewrites, plus the template for ~20% (always when no rewrite is good)."""
    from ...translation.prompt import _same
    out, seen, reasons = [], set(), Counter()
    for _, text in rewrites:
        bad = M.problems(text, item)
        for b in bad:
            reasons[b.split(":")[0]] += 1
        if not bad and text.lower() not in seen and text != item["template"] and len(out) < 2:
            seen.add(text.lower())
            out.append(text)
    if not out or rng.random() < M.TEMPLATE_SHARE:
        out.append(item["template"])
    near = [q for q in out if any(_same(q, h) for h in held)]
    reasons["like a benchmark or examples.md question"] += len(near)
    unannounced = [q for q in out if q not in near and P.type_problems(q, item["plan"])]
    reasons["a record type the question does not announce"] += len(unannounced)
    return [q for q in out if q not in near and q not in unannounced], reasons


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", type=int, default=190)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true", help="template sentences only (no rewording)")
    args = ap.parse_args()
    t0 = time.time()
    M.CACHE.mkdir(parents=True, exist_ok=True)
    pairs, rejected = sample(args.pairs, args.seed)
    print(f"[{time.time() - t0:5.0f} s] {len(pairs)} pairs; rejected: {dict(rejected)}", flush=True)
    rewrites = {}
    if not args.no_llm:
        M.wake()
        cache = M.Cache(M.CACHE / "rewrites_pairs.jsonl")
        for organizers in (False, True):
            part = [it for i, p in enumerate(pairs) if i % 2 == organizers for it in p]
            got, n_retry = M.reword_items(part, cache, M.PAIRS_ORGANIZERS if organizers else None)
            rewrites.update(got)
            print(f"[{time.time() - t0:5.0f} s] rewording ({'organizers' if organizers else 'mixed'} voice): "
                  f"{len(part)} plans, {n_retry} retried", flush=True)
    rng, held, reasons = random.Random(args.seed + 1), M.held_out(), Counter()
    lines, by_kind = {"train": [], "val": []}, Counter()
    for p in pairs:
        split = "val" if int(M.sha("split", p[0]["id"]), 16) % 100 < M.VAL_SHARE * 100 else "train"
        for it in p:
            qs, why = questions(it, rewrites.get(it["id"], []), rng, held)
            reasons.update(why)
            by_kind[it["pair"]] += len(qs)
            lines[split] += [M.row(q, it["plan"]) for q in qs]
    for split, rows in lines.items():
        rng.shuffle(rows)
        (OUT / f"{split}_pairs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"[{time.time() - t0:5.0f} s] {OUT}: train_pairs {len(lines['train'])}, val_pairs {len(lines['val'])} "
          f"questions from {2 * len(pairs)} plans; by kind: {dict(by_kind)}; rewrite rejections: {dict(reasons.most_common(8))}")


if __name__ == "__main__":
    main()
