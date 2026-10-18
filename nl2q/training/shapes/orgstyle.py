"""Training data in the organizers' question style (tests/bench/official.jsonl names no codes, asks with vague words and
mostly for breakdowns with 3 to 5 outputs, the most common first). About 2,000 plans written by code, plus 1,000 whose
templates name no code at all (--plain), a template sentence per plan in those words, two rewrites each by the
generator (training.make's reword / problems, the organizers' voice for all, training.make.PAIRS_ORGANIZERS), kept only
when the vague cue words stay. Conventions of every plan:
- "how long" a record lasts (a drug administration, a procedure, a measurement) -> its hours (r.hours), a rounded avg;
- the time between two records ("how long until", "how long do they wait") -> a gap in days, rounded;
- "how often" / "how many of them" -> a count (patients_if); "what share" / "how likely" -> share;
- "most common", "what kinds of" -> one row per code class or coded detail, ordered by the count, largest first;
- every breakdown is ordered by its first count, largest first.
No template states the unit or the word percent the convention supplies. About 80% are breakdowns over one record type
(by its code class, with ns / match for code families, by a coded detail, by sex or by year); 20% the newer shapes
(stay_has group, after / next with a gap, compare with its has). No plan is an organizers' right plan
(resources/plans.jsonl org-*, O02, O04), nor has the same record types and grouping with the same outputs (or all but
one of them); no question is like an official one. Every plan passes check() and compile().
Writes nl2q_data/training/train_orgstyle.jsonl and val_orgstyle.jsonl (5% of the plans), one row per question.

python -m nl2q.training.shapes.orgstyle [--plans 2000] [--plain 1000] [--seed 0] [--no-llm]
(--plain: more plans whose templates name no code: only codes with a plain name, coded values said in words)"""
import argparse, json, random, re, time
from ...translation import plan as P
from .. import make as M

WORKERS = 40
# record type: (variable, singular, plural, cue a rewrite must keep)
KINDS = {
    "BilledDiagnosis": ("dx", "billed diagnosis", "billed diagnoses", r"diagnos"),
    "OncologyDiagnosis": ("onco", "cancer diagnosis", "cancer diagnoses", r"cancer|oncolog|tumou?r|malignan|neoplas"),
    "NursingDiagnosis": ("nursing", "nursing diagnosis", "nursing diagnoses", r"nurs"),
    "ProblemCondition": ("problem", "recorded problem", "recorded problems", r"problem|condition"),
    "LabTestEvent": ("lab", "lab test", "lab tests", r"lab|test"),
    "DrugAdministrationEvent": ("drug", "drug administration", "drug administrations",
                                r"drug|medic|administ|dose|given|treat|therap|infusion"),
    "BilledProcedure": ("proc", "billed procedure", "billed procedures", r"procedur|operat|intervention"),
    "MedicalProcedure": ("medproc", "medical procedure", "medical procedures", r"procedur|operat|intervention"),
    "OncologySurgery": ("surgery", "cancer surgery", "cancer surgeries", r"surg|operat|resect"),
    "RadiotherapyProcedure": ("rt", "radiotherapy session", "radiotherapy sessions", r"radio|radiation|irradiat"),
    "Allergy": ("allergy", "allergy", "allergies", r"allerg"),
    "AdverseEvent": ("ae", "adverse event", "adverse events", r"adverse|side effect|reaction|event"),
    "Death": ("death", "death", "deaths", r"death|died|dies|deceased|mortal|passed"),
    "AdministrativeCase": ("stay", "hospital stay", "hospital stays", r"admi|hospital|stay"),
    "BodyTemperatureMeasurement": ("temp", "temperature measurement", "temperature measurements", r"temperature|fever"),
    "HeartRateMeasurement": ("hr", "heart rate measurement", "heart rate measurements", r"heart|pulse"),
    "BloodPressureMeasurement": ("bp", "blood pressure measurement", "blood pressure measurements", r"blood pressure|\bbp\b"),
    "BodyWeightMeasurement": ("weight", "weight measurement", "weight measurements", r"weigh"),
    "BodyHeightMeasurement": ("height", "height measurement", "height measurements", r"height|tall"),
    "OxygenSaturationMeasurement": ("spo2", "oxygen saturation measurement", "oxygen saturation measurements",
                                    r"oxygen|saturation|spo2"),
    "RespiratoryRateMeasurement": ("resp", "respiratory rate measurement", "respiratory rate measurements",
                                   r"respirat|breath"),
    "BodyMassIndex": ("bmi", "BMI record", "BMI records", r"\bbmi\b|body mass"),
    "LabResult": ("result", "lab result", "lab results", r"lab|result"),
    "VariantDescriptor": ("vd", "genetic variant", "genetic variants", r"variant|genetic|genomic|mutation"),
    "Birth": ("birth", "birth", "births", r"birth|born|bab|deliver|newborn"),
}
UNIT_WEIGHTS = {"BilledDiagnosis": 5, "OncologyDiagnosis": 4, "NursingDiagnosis": 2, "ProblemCondition": 3,
                "LabTestEvent": 4, "DrugAdministrationEvent": 6, "BilledProcedure": 3, "MedicalProcedure": 4,
                "OncologySurgery": 3, "RadiotherapyProcedure": 4, "Allergy": 5, "AdverseEvent": 5, "Death": 4,
                "AdministrativeCase": 1, "BodyTemperatureMeasurement": 1.5, "HeartRateMeasurement": 1.5,
                "BloodPressureMeasurement": 1, "BodyWeightMeasurement": 1.5, "BodyHeightMeasurement": 1,
                "OxygenSaturationMeasurement": 1.2, "RespiratoryRateMeasurement": 1, "BodyMassIndex": 1.5,
                "LabResult": 3, "VariantDescriptor": 4, "Birth": 4}
# what "kinds" means when a record type is grouped by its own code
CODE_KINDS = {"BilledDiagnosis": "billed diagnoses", "OncologyDiagnosis": "cancer diagnoses",
              "NursingDiagnosis": "nursing diagnoses", "ProblemCondition": "recorded problems",
              "LabTestEvent": "lab tests", "DrugAdministrationEvent": "administered drugs",
              "BilledProcedure": "billed procedures", "MedicalProcedure": "medical procedures",
              "OncologySurgery": "cancer surgeries", "RadiotherapyProcedure": "radiotherapy treatments",
              "Allergy": "allergies", "AdverseEvent": "adverse events", "Death": "causes of death",
              "BodyTemperatureMeasurement": "temperature measurements", "HeartRateMeasurement": "heart rate measurements",
              "BloodPressureMeasurement": "blood pressure measurements", "BodyWeightMeasurement": "weight measurements",
              "BodyHeightMeasurement": "height measurements", "OxygenSaturationMeasurement": "oxygen saturation readings",
              "RespiratoryRateMeasurement": "respiratory rate readings", "LabResult": "lab results"}
# a coded detail's kinds (plural noun) and the cue a rewrite keeps
DETAIL_WORDS = {"rank": ("ranks (main or secondary)", r"rank|main|principal|primary|secondary"),
                "route": ("administration routes", r"route|way|given|administ"),
                "reason_to_stop": ("reasons for stopping", r"stop|discontinu|halt|end"),
                "subject_pregnancy_status": ("pregnancy statuses of the patient", r"pregnan"),
                "relative_temporality": ("relative temporalities", r"temporal|timing|time"),
                "reaction_type": ("reaction types", r"reaction"), "severity": ("severity levels", r"sever|critical|grade"),
                "verification_status": ("verification statuses", r"verif|confirm|status"),
                "consequences": ("kinds of consequences", r"consequen|outcome|impact|result"),
                "outcome": ("outcomes", r"outcome|end|result|recover"),
                "circumstance": ("circumstances", r"circumstanc|context|how|setting"),
                "method": ("measurement methods", r"method|how .* measured|technique|device|way"),
                "cell_count_estimate": ("cell count estimates", r"cell count|count"),
                "cell_morphology": ("cell morphologies", r"morpholog|shape"),
                "cell_organization": ("cell organisation patterns", r"organi[sz]"),
                "finding": ("findings", r"finding"), "presence": ("presence results", r"presen|absen|detect"),
                "quantity_estimate": ("quantity estimates", r"quantit|amount"),
                "staining_result": ("staining results", r"stain"),
                "allele_origin": ("allele origins", r"allele|origin|inherit|germline|somatic"),
                "type": ("variant types", r"type|kind|class"),
                "variation": ("kinds of genomic variation", r"variation|kind|type"),
                "zygosity": ("zygosities", r"zygos|copies|copy|heterozyg|homozyg"),
                "delivery_mode": ("delivery modes", r"deliver|mode|caesar|vaginal|born"),
                "insurance": ("insurance coverage types", r"insur|coverage|cover")}
# value thresholds of the measurements with one unit: (op, value, words)
VALUE_CONDS = {"BodyTemperatureMeasurement": [(">", 38, "show a temperature above 38 degrees"),
                                              (">=", 39, "show a temperature of 39 degrees or more")],
               "HeartRateMeasurement": [(">", 100, "show a heart rate above 100"), ("<", 50, "show a heart rate below 50")],
               "OxygenSaturationMeasurement": [("<", 90, "show a saturation below 90"), ("<", 94, "show a saturation below 94")],
               "BodyWeightMeasurement": [(">", 100, "show a weight over 100 kg"), ("<", 50, "show a weight under 50 kg")],
               "BodyHeightMeasurement": [(">=", 190, "measure 190 cm or more"), ("<", 155, "measure under 155 cm")],
               "RespiratoryRateMeasurement": [(">", 20, "show more than 20 breaths a minute"),
                                              ("<", 12, "show fewer than 12 breaths a minute")],
               "BodyMassIndex": [(">=", 30, "show a BMI of 30 or more"), ("<", 18.5, "show a BMI below 18.5")]}
VALUE_WORDS = {"BodyTemperatureMeasurement": ("how high is the temperature usually", r"temperature"),
               "HeartRateMeasurement": ("how fast is the heart rate usually", r"heart|pulse|rate"),
               "OxygenSaturationMeasurement": ("what is the usual oxygen saturation", r"saturation|oxygen|spo2"),
               "BodyWeightMeasurement": ("how much do the patients usually weigh", r"weigh"),
               "BodyHeightMeasurement": ("how tall are the patients usually", r"tall|height"),
               "RespiratoryRateMeasurement": ("how fast do the patients usually breathe", r"breath|respirat|rate"),
               "BodyMassIndex": ("what is the usual BMI", r"\bbmi\b|body mass"),
               "LabTestEvent": ("what is the usual result value", r"value|result|level"),
               "LabResult": ("what is the usual value", r"value|result|level")}
AGE_CONDS = [(">=", 65, "were 65 or older at the time"), (">=", 80, "were 80 or older at the time"),
             ("<", 18, "were under 18 at the time"), ("<", 40, "were younger than 40 at the time")]
HOURS_CONDS = (2, 4, 6, 12, 24, 48, 72)
GESTATION_CONDS = (224, 238, 245, 252)  # days (never the organizers' 259)
OP_CUES = {">": M.CUE_OPS[">"], "<": M.CUE_OPS["<"], ">=": M.CUE_OPS[">="]}
USUAL = r"usual|typical|average|mean|normally|generally|tend|on average|ordinarily|regularly|common"
NO_UNIT = r"\bhours?\b|\bhrs?\b|\bminutes?\b|\bdays?\b|\bweeks?\b"
NO_PERCENT = r"percent|%"
MOST = r"most|common|frequent|top|leading|kinds?|types?|which|rank|popular|often"
STAY_TYPES = [t for t in P.STAY_TYPES if t in KINDS and t not in ("VariantDescriptor", "Birth", "BodyMassIndex")]
# what may be counted next to a record type as "also have" (patients_if has)
OTHERS = {"Death": 4, "OncologyDiagnosis": 3, "Allergy": 2, "AdverseEvent": 2, "BilledDiagnosis": 3,
          "RadiotherapyProcedure": 1, "DrugAdministrationEvent": 1, "OncologySurgery": 1, "MedicalProcedure": 1}
# after/next pathways (never the organizers' example 1 types)
STARTS = {"BilledDiagnosis": 5, "OncologyDiagnosis": 2, "ProblemCondition": 2, "LabTestEvent": 3, "NursingDiagnosis": 1,
          "AdverseEvent": 1, "Allergy": 1, "MedicalProcedure": 1}
NEXTS = {"BilledProcedure": 4, "MedicalProcedure": 3, "DrugAdministrationEvent": 5, "LabTestEvent": 2, "Death": 2,
         "RadiotherapyProcedure": 1, "OncologySurgery": 1, "AdverseEvent": 1}
O01_TYPES = {"OncologyDiagnosis", "OncologySurgery", "RadiotherapyProcedure"}
CODE_PAIRS = [("BilledDiagnosis", "ProblemCondition"), ("BilledDiagnosis", "NursingDiagnosis"),
              ("BilledDiagnosis", "OncologyDiagnosis"), ("BilledProcedure", "MedicalProcedure"),
              ("BilledDiagnosis", "Death"), ("MedicalProcedure", "OncologySurgery")]
DATE_PAIRS = [("LabTestEvent", "DrugAdministrationEvent"), ("BilledDiagnosis", "BilledProcedure"),
              ("BilledProcedure", "LabTestEvent"), ("MedicalProcedure", "DrugAdministrationEvent")]


def a_(word):
    return ("an " if word[0].lower() in "aeiou" else "a ") + word


def join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]


def weighted(rng, table):
    items = list(table.items()) if isinstance(table, dict) else list(table)
    return rng.choices([k for k, _ in items], [w for _, w in items])[0]


def cached_names(pool):
    """{icd-10-gm code: plain name}: the names training.make's cache holds that pass its round trip (no model call)."""
    cache = M.Cache(M.CACHE / "names.jsonl")
    out = {}
    for c in sorted({c for t in ("BilledDiagnosis", "OncologyDiagnosis") for c, _ in pool.get(t, [])[:80]}):
        v = cache.get(M.sha("name-v2", M.LLM_MODEL, c)) or {}
        if v.get("known") and v.get("name") and v.get("back") == c.split(":", 1)[1]:
            out[c] = v["name"]
    return out


class Ctx:
    """What the sampler draws from: codes (data inventory), plain names, coded details' values."""

    def __init__(self, pool, names, dvals, plain=False):
        self.pool, self.names, self.dvals, self.plain = pool, names, dvals, plain


def codes_of(ctx, t):
    """The codes a plan may name for a record type (never the official questions' cholera nor CHOP 00.0); plain: only
    the codes with a plain name, so the template names no code (the official questions name none)."""
    return [c for c, _ in ctx.pool.get(t, [])[:40] if not c.startswith("icd-10-gm:A00") and not c.startswith("chop:00.0")
            and (not ctx.plain or c in ctx.names)]


def code_words(ctx, rng, code, keep, prefer_name=0.9):
    """How a template names a code: its plain name when the cache has one, else the code with its system."""
    if code in ctx.names and (ctx.plain or rng.random() < prefer_name):
        keep.names.add(ctx.names[code])
        return ctx.names[code], True
    system, local = code.split(":", 1)
    keep.exact.add(local)
    keep.cue("code system", {"icd-10-gm": r"icd", "snomed": r"snomed", "chop": r"chop", "loinc": r"loinc",
                             "geno": r"geno", "so": r"\bso\b|sequence ontology"}.get(system, ""))
    return f"{M.SYSTEMS.get(system, M.SYSNAME.get(system, system))} code {local}", False


def noun_phrase(ctx, rng, t, rec, keep):
    """The record's plural noun with its code ("billed diagnoses of type 2 diabetes", "allergies with SNOMED CT code ...")."""
    plural = KINDS[t][2]
    keep.cue(KINDS[t][1], KINDS[t][3])
    if rec.get("code"):
        words, named = code_words(ctx, rng, rec["code"][0], keep)
        return f"{plural} of {words}" if named else f"{plural} with {words}"
    return plural


def other_record(ctx, rng, t, keep, exclude=()):
    """(record spec, its words with an article) of another record a plan names (has, stay_has)."""
    spec = {"type": t}
    codes = codes_of(ctx, t)
    one = KINDS[t][1]
    keep.cue(one, KINDS[t][3])
    if codes and rng.random() < 0.55:
        named = [c for c in codes if c in ctx.names]
        code = rng.choice(named) if named and rng.random() < 0.8 else rng.choice(codes)
        spec["code"] = [code]
        words, is_name = code_words(ctx, rng, code, keep)
        return spec, (f"{a_(one)} of {words}" if is_name else f"{a_(one)} with {words}")
    return spec, a_(one)


# ---- conditions: (if, slug, declarative "of them ...", infinitive "to ...") -------------------------------------------

def detail_value(ctx, rng, t, a, keep):
    """(value, words after "have") of one coded detail value."""
    if (t, a) in M.DETAILS and ctx.dvals.get((t, a)) and a != "rank":
        test, _, have = M.detail_test(rng, t, "x", a, ctx.dvals, keep)
        return test["value"], have
    d = P.CODED_VALUES.get(t, {}).get(a)
    if not d:
        return None, None
    if d["kind"] in ("value", "class"):
        v = rng.choice(d["values"])
        shown = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", v.split(":")[-1]).lower()
        keep.cue(a, re.escape(shown.split()[0][:6]))
        return v, f"have {DETAIL_WORDS.get(a, (a,))[0].rstrip('s')} {shown}" if d["kind"] == "value" else f"are a {shown}"
    codes = d.get("codes") if d.get("closed") else (d.get("examples") or d.get("codes"))
    if not codes:
        return None, None
    v = rng.choice(codes)
    words, _ = code_words(ctx, rng, v, keep, 0)
    return v, f"have {a.replace('_', ' ')} {words}"


def conditions(ctx, rng, t, v, grouped, stats_on, keep):
    """Candidate conditions of a breakdown over record v of type t: [(kind, if, slug, decl, inf, extra records, cues)];
    kind "attr" (share or patients_if), "has" (patients_if only), "stay" (share or patients_if)."""
    spec, out = P.CURATED[t], []
    if spec.get("rank") and grouped != "rank":
        word = "the main diagnosis" if "Diagnosis" in t else "the principal procedure"
        out.append(("attr", {"attr": f"{v}.rank", "op": "=", "value": "main"}, "main", f"are {word}", f"be {word}", {},
                    [("main", r"main|principal|primary")]))
    if P.has_alias(spec, "in_stay"):
        out.append(("attr", {"attr": f"{v}.in_stay", "op": "=", "value": True}, "in_stay",
                    "begin during a hospital stay", "begin during an inpatient stay", {},
                    [("in stay", r"hospital|stay|admi|inpatient|ward")]))
    if P.has_alias(spec, "hours") and "hours" not in stats_on:
        n = rng.choice(HOURS_CONDS)
        out.append(("attr", {"attr": f"{v}.hours", "op": ">", "value": n}, f"over_{n}h", f"last more than {n} hours",
                    f"last more than {n} hours", {}, [("hours", r"hour|\bh\b|hrs?"), ("more than", OP_CUES[">"])], n))
    if t in VALUE_CONDS:
        op, n, words = rng.choice(VALUE_CONDS[t])
        slug = ("over_" if ">" in op else "under_") + M._num(n).replace(".", "_")
        out.append(("attr", {"attr": f"{v}.value", "op": op, "value": n}, slug, words, words, {},
                    [("threshold", OP_CUES[op])], n))
    if spec.get("age") and "age" not in stats_on:
        op, n, words = rng.choice(AGE_CONDS)
        out.append(("attr", {"attr": f"{v}.age", "op": op, "value": n}, ("age_" if ">" in op else "under_") + str(n),
                    words, words.replace("were", "be"), {}, [("age", OP_CUES[op] + r"|older|younger|aged?")], n))
    if t == "Birth" and "gestational_age" not in stats_on:
        n = rng.choice(GESTATION_CONDS)
        out.append(("attr", {"attr": f"{v}.gestational_age", "op": "<", "value": n}, f"under_{n}d",
                    f"are born before {n} days of gestation", f"be born before {n} days of gestation", {},
                    [("gestation", r"gestation|pregnan"), ("before", OP_CUES["<"] + r"|before|earlier")], n))
    for a in [a for a in P.coded(t) if a != grouped] + (["route"] if spec.get("route") and grouped != "route" else []):
        if t == "VariantDescriptor" and a == "zygosity" and grouped == "variation":
            continue  # the organizers' example 8
        k = Keep2()
        value, have = detail_value(ctx, rng, t, a, k)
        if value is None or ctx.plain and k.exact:  # plain: only values said in words (Fatal, homozygous, ...)
            continue
        inf = have if not have.startswith("are ") else "be " + have[4:]
        slug = M.nm(a[:8], str(value).split(":")[-1])[:20]
        out.append(("attr", {"attr": f"{v}.{a}", "op": "=", "value": value}, slug, have, inf, {}, k))
    # never a variant's cancer flag (the organizers' example 8)
    o = weighted(rng, {o: w for o, w in OTHERS.items()
                       if o != t and not (t == "VariantDescriptor" and o == "OncologyDiagnosis")})
    k = Keep2()
    ospec, words = other_record(ctx, rng, o, k)
    out.append(("has", {"has": KINDS[o][0]}, "with_" + KINDS[o][0], f"also have {words}", f"also have {words}",
                {KINDS[o][0]: ospec}, k))
    if t in STAY_TYPES:
        b = rng.choice([x for x in STAY_TYPES if x != t and x not in ("AdministrativeCase",)])
        k = Keep2()
        bspec, words = other_record(ctx, rng, b, k)
        k.cue("stay", r"stay|admission|hospitali")
        words = f"fall in a hospital stay that also has {words}"
        out.append(("stay", {"stay_has": KINDS[b][0]}, "stay_" + KINDS[b][0], words, words, {KINDS[b][0]: bspec}, k))
    return out


class Keep2(M.Keep):
    """A Keep for one part of a template, merged into the plan's when the part is used."""

    def merge_into(self, keep):
        keep.exact |= self.exact
        keep.numbers |= self.numbers
        keep.dates |= self.dates
        keep.names |= self.names
        keep.cues += self.cues
        keep.forbid += self.forbid


def stats(rng, t, v):
    """Candidate statistics of record v: [(attr alias, [outputs], words, cues, forbid)]."""
    spec, out = P.CURATED[t], []
    if P.has_alias(spec, "hours"):
        if rng.random() < 0.75:
            words = rng.choice(("how long do they usually last", "how long does each one usually take",
                                "how long do they typically run", "how long do they last on average"))
            out.append(("hours", [{"name": "avg_hours", "agg": "avg", "of": f"{v}.hours", "round": True}], words,
                        [("how long", r"how long|duration|last|take|length|time|run"), ("usually", USUAL)], [NO_UNIT]))
        else:
            out.append(("hours", [{"name": "min_hours", "agg": "min", "of": f"{v}.hours", "round": True},
                                  {"name": "max_hours", "agg": "max", "of": f"{v}.hours", "round": True}],
                        "what are the shortest and the longest they last",
                        [("shortest", r"shortest|minimum|quickest|briefest|least|min"),
                         ("longest", r"longest|maximum|most|max")], [NO_UNIT]))
    if t in VALUE_WORDS and not (t in M.NO_VALUE):
        words, cue = VALUE_WORDS[t]
        out.append(("value", [{"name": "avg_value", "agg": "avg", "of": f"{v}.value", "round": True}], words,
                    [("value", cue), ("usually", USUAL)], []))
    if spec.get("age"):
        if rng.random() < 0.8:
            out.append(("age", [{"name": "avg_age", "agg": "avg", "of": f"{v}.age", "round": True}],
                        rng.choice(("how old are the patients usually", "how old are they typically at the time")),
                        [("how old", r"how old|\bage|aged"), ("usually", USUAL)], []))
        else:
            out.append(("age", [{"name": "min_age", "agg": "min", "of": f"{v}.age", "round": True},
                                {"name": "max_age", "agg": "max", "of": f"{v}.age", "round": True}],
                        "how old are the youngest and the oldest patients",
                        [("youngest", r"youngest|minimum|lowest|min"), ("oldest", r"oldest|maximum|highest|max")], []))
    if t == "Birth":
        out.append(("gestational_age", [{"name": "avg_gestational_age", "agg": "avg", "of": f"{v}.gestational_age",
                                         "round": True}],
                    "how far along is the pregnancy usually at birth",
                    [("gestation", r"gestation|pregnan|far along|term"), ("usually", USUAL)], [NO_UNIT]))
    return out


# ---- the shapes --------------------------------------------------------------------------------------------------------

def group_options(ctx, t):
    """[(kind, alias or None, weight)] of the groupings of a record type."""
    spec, out = P.CURATED[t], []
    if spec.get("code") and t != "VariantDescriptor":
        out.append(("code", None, 4))
    for a in P.coded(t):
        out.append(("detail", a, 3))
    for a in ("rank", "route"):
        if spec.get(a):
            out.append(("detail", a, 2))
    if "insurance" in P.extra(t):
        out.append(("detail", "insurance", 0.7))
    out.append(("sex", None, 1.2))
    if spec.get("date"):
        out.append(("year", None, 1.2))
    return out


def breakdown(ctx, rng):
    """(plan, template, keep) of one breakdown over one record type."""
    keep = M.Keep()
    t = weighted(rng, UNIT_WEIGHTS)
    v, one, plural, cue = KINDS[t]
    gkind, alias = weighted(rng, [((k, a), w) for k, a, w in group_options(ctx, t)])
    rec = {"type": t}
    group, lead = None, None
    codes = codes_of(ctx, t)
    if gkind == "code":
        ns = (ctx.pool.get(t) or [[None]])[0][0]
        ns = ns.split(":")[0] if ns else P.CURATED[t]["systems"][0]
        group = {"code_class": v, "ns": ns}
        kinds = CODE_KINDS[t]
        fam = [c for c in codes if c.startswith("icd-10-gm:") and "." not in c]
        if fam and rng.random() < 0.45:
            parent = rng.choice(fam)
            rec["code"] = [parent]
            local = parent.split(":")[1]
            group["match"] = "^" + re.escape(local) + r"\."
            words, named = code_words(ctx, rng, parent, keep)
            kinds = f"kinds of {words}" if named else f"{words} sub-codes"
            lead = rng.choice((f"Which {kinds} do we see most among our {plural}",
                               f"What are the most common {kinds} in our {plural}"))
        keep.cue(one, cue)
        if lead is None:
            lead = rng.choice((f"What are the most common {kinds}", f"Which {kinds} come up most",
                               f"What kinds of {kinds} do we see most"))
        keep.cue("most common", MOST)
    elif gkind == "detail":
        group = {"code_class": f"{v}.{alias}"}
        snomed = (P.CODED_VALUES.get(t, {}).get(alias) or {}).get("systems") == ["snomed"]
        if alias in ("route", "rank") or P.attr_kind(t, alias) == "code" and snomed:
            if alias in ("route", "rank") or rng.random() < 0.5:
                group["ns"] = "snomed"
        if alias == "rank" or rng.random() < 0.25 and codes:
            if codes and rng.random() < 0.6:
                rec["code"] = [rng.choice(codes)]
        words, dcue = DETAIL_WORDS.get(alias, (alias.replace("_", " ") + "s", re.escape(alias[:5])))
        np_ = noun_phrase(ctx, rng, t, rec, keep)
        keep.cue(alias, dcue)
        keep.cue("most common", MOST)
        lead = rng.choice((f"What are the most common {words} of our {np_}", f"Which {words} of {np_} come up most",
                           f"For {np_}, what kinds of {words} do we see most"))
    elif gkind == "sex":
        group = {"sex": True}
        if codes and rng.random() < 0.5:
            rec["code"] = [rng.choice(codes)]
        np_ = noun_phrase(ctx, rng, t, rec, keep)
        keep.cue("sex", r"sex|gender|\bmen\b|women|male|female")
        lead = rng.choice((f"For men and women separately, looking at {np_}", f"Split by sex, for {np_}",
                           f"Comparing women and men with {np_}"))
    else:
        group = {"year": f"{v}.date"}
        if codes and rng.random() < 0.5:
            rec["code"] = [rng.choice(codes)]
        np_ = noun_phrase(ctx, rng, t, rec, keep)
        keep.cue("year", r"year|annual")
        lead = rng.choice((f"Year by year, for {np_}", f"For each year, looking at {np_}", f"Per year, for {np_}"))
    recs = {v: rec}
    plan = {"route": "plan", "records": recs, "group": [group]}
    # the outputs: a count first, then statistics and conditions, 3 to 5 in all
    budget = rng.choices((3, 4, 5), (40, 40, 20))[0]
    out, parts = [], []
    flags = rng.random() < 0.25  # patients and patients_if only, so "also have" another record can be counted
    first = "patients" if flags or rng.random() < 0.7 else "records"
    each = gkind in ("code", "detail")
    count_words = {"patients": rng.choice(("how many patients have each", "for how many patients") if each else
                                          ("how many patients are there", "how many patients have them")),
                   "records": rng.choice((f"how many {plural} are there", "how many times does each come up",
                                          f"how many {plural} do we count") if each else
                                         (f"how many {plural} are there", f"how many {plural} do we count"))}
    for c in [first] + (["records" if first == "patients" else "patients"] if not flags and rng.random() < 0.3 else []):
        out.append({"name": c, "agg": c, **({"of": v} if c == "records" else {})})
        parts.append(count_words[c])
        keep.cue(c, r"how many|number|count|how often|frequen|times")
    st = stats(rng, t, v) if not flags else []
    rng.shuffle(st)
    chosen_stats = []
    for s_ in st:
        if len(out) + sum(len(x[1]) for x in chosen_stats) + len(s_[1]) <= budget - 1 or not chosen_stats and rng.random() < 0.5 \
                and len(out) + len(s_[1]) <= budget:
            chosen_stats.append(s_)
            if rng.random() < 0.6:
                break
    for s_ in chosen_stats:
        out += s_[1]
        parts.append(s_[2])
        for label, rx in s_[3]:
            keep.cue(label, rx)
        keep.forbid += s_[4]
    stats_on = {s_[0] for s_ in chosen_stats}
    conds = [c for c in conditions(ctx, rng, t, v, alias, stats_on, keep) if (c[0] == "has") <= flags
             and not (flags and c[0] == "stay")]
    rng.shuffle(conds)
    conds.sort(key=lambda c: c[0] != "has")  # a flags breakdown counts "also have" first
    used, has_flag, stay_flag = [], False, False
    for c in conds:
        if len(out) + len(used) >= budget:
            break
        if c[0] == "has" and stay_flag or c[0] == "stay" and has_flag:
            continue
        used.append(c)
        has_flag |= c[0] == "has"
        stay_flag |= c[0] == "stay"
    names_used = {m["name"] for m in out}
    share_used = False
    for c in used:
        kind, cond, slug, decl, inf, extra_recs, ck = c[:7]
        agg = "patients_if" if kind == "has" or flags else rng.choice(("patients_if", "share"))
        name = (f"patients_{slug}" if agg == "patients_if" else f"{slug}_pct")[:24].rstrip("_")
        if name in names_used:
            continue
        names_used.add(name)
        out.append({"name": name, "agg": agg, "if": cond})
        recs.update(extra_recs)
        if isinstance(ck, M.Keep):
            ck.merge_into(keep)
        else:
            for label, rx in ck:
                keep.cue(label, rx)
        if len(c) > 7:
            keep.number(c[7])
            if cond.get("op") in (">", "<"):
                keep.forbid.append(M.INCLUSIVE)
        if agg == "patients_if":
            q = rng.choice((f"how many of them {decl}",
                            ("how often are they " + inf[3:]) if inf.startswith("be ") else f"how often do they {inf}"))
            keep.cue("how often", r"how often|how many|how frequently|number|count|frequen")
        else:
            share_used = True
            q = rng.choice((f"what share of them {decl}", f"how likely are they to {inf}"))
            keep.cue("share", r"share|proportion|fraction|portion|how likely|likel|probab|odds|chance|rate|ratio|"
                              r"how much of|what part")
        parts.append(q)
    if share_used:
        keep.forbid.append(NO_PERCENT)
    if not any(m["agg"] == "share" for m in out) and any(m["agg"] == "patients_if" for m in out):
        keep.forbid.append(r"percent|%|\bproportion|\bshare\b|\bfraction|\bratio\b")
    if t == "Birth" and any("gestational_age" in json.dumps(m) for m in out):
        rec["where"] = [{"attr": f"{v}.gestational_age_unit", "op": "=", "value": "ucum:d"}]
    plan["output"] = out
    plan["order"] = [{"by": out[0]["name"], "desc": True}]
    if gkind in ("code", "detail") and rng.random() < 0.15:
        n = rng.choice((3, 5, 10))
        plan["limit"] = n
        lead = lead.replace("most common", f"{n} most common", 1) if "most common" in lead else lead + f" (the top {n})"
        keep.number(n)
    if not 3 <= len(out) <= 5:
        return None
    text = lead + ", " + join(parts) + "?"
    text = text[0].upper() + text[1:]
    return plan, text, keep


def stay_split(ctx, rng):
    """Rows of one record type split by whether their stay also has another record (group stay_has)."""
    keep = M.Keep()
    t = weighted(rng, {x: UNIT_WEIGHTS[x] for x in STAY_TYPES if x != "AdministrativeCase"})
    v, one, plural, cue = KINDS[t]
    b = rng.choice([x for x in STAY_TYPES if x not in (t, "AdministrativeCase")])
    k = Keep2()
    bspec, bwords = other_record(ctx, rng, b, k)
    k.merge_into(keep)
    rec = {"type": t}
    codes = codes_of(ctx, t)
    if codes and rng.random() < 0.3:
        rec["code"] = [rng.choice(codes)]
    np_ = noun_phrase(ctx, rng, t, rec, keep)
    bname = KINDS[b][0]
    out, parts = [], []
    first = "patients" if rng.random() < 0.6 else "records"
    out.append({"name": first, "agg": first, **({"of": v} if first == "records" else {})})
    parts.append("how many patients have them" if first == "patients" else f"how many {plural} are there")
    st = stats(rng, t, v)
    rng.shuffle(st)
    chosen = st[:rng.choice((1, 1, 2))]
    for s in chosen:
        out += s[1]
        parts.append(s[2])
        for label, rx in s[3]:
            keep.cue(label, rx)
        keep.forbid += s[4]
    conds = [c for c in conditions(ctx, rng, t, v, None, {s[0] for s in chosen}, keep) if c[0] == "attr"]
    rng.shuffle(conds)
    for c in conds[:rng.choice((0, 1, 1, 2)) if chosen else 1]:
        kind, cond, slug, decl, inf, _, ck = c[:7]
        agg = rng.choice(("patients_if", "share"))
        name = (f"patients_{slug}" if agg == "patients_if" else f"{slug}_pct")[:24].rstrip("_")
        out.append({"name": name, "agg": agg, "if": cond})
        if isinstance(ck, M.Keep):
            ck.merge_into(keep)
        else:
            for label, rx in ck:
                keep.cue(label, rx)
        if len(c) > 7:
            keep.number(c[7])
            if cond.get("op") in (">", "<"):
                keep.forbid.append(M.INCLUSIVE)
        parts.append(f"how many of them {decl}" if agg == "patients_if" else f"what share of them {decl}")
        if agg == "share":
            keep.forbid.append(NO_PERCENT)
    if len(out) < 3:
        return None
    if t == "DrugAdministrationEvent" and b == "BilledDiagnosis":
        return None  # the organizers' example 2
    keep.cue("stay", r"stay|admission|hospitali")
    keep.cue("compared", r"other|without|compar|versus|vs|rest|those that|not")
    plan = {"route": "plan", "records": {v: rec, bname: bspec}, "group": [{"stay_has": bname}], "output": out,
            "order": [{"by": out[0]["name"], "desc": True}]}
    lead = rng.choice((f"In hospital stays that also have {bwords}, compared with stays without one, for {np_}",
                       f"For {np_}, comparing stays that also include {bwords} with the other stays"))
    return plan, lead + ", " + join(parts) + "?", keep


def pathway(ctx, rng):
    """After a first record, which records follow it, and how long until the first of them (group after/next, gap)."""
    keep = M.Keep()
    start = weighted(rng, STARTS)
    nexts = []
    for _ in range(rng.choices((1, 2), (40, 60))[0]):
        nexts.append(weighted(rng, {k: w for k, w in NEXTS.items() if k != start and k not in nexts}))
    if {start, *nexts} >= O01_TYPES or start == "OncologyDiagnosis" and set(nexts) & (O01_TYPES - {start}):
        return None
    a = KINDS[start][0]
    k = Keep2()
    aspec, awords = other_record(ctx, rng, start, k)
    k.merge_into(keep)
    recs = {a: aspec}
    nwords = []
    for n in nexts:
        recs[KINDS[n][0]] = {"type": n}
        nwords.append(KINDS[n][1])
        keep.cue(KINDS[n][1], KINDS[n][3])
    names = [KINDS[n][0] for n in nexts]
    out = [{"name": "patients", "agg": "patients"},
           {"name": "avg_days_to_next", "agg": "gap", "stat": "avg", "from": f"{a}.date", "to": [f"{n}.date" for n in names],
            "in": "days", "round": True}]
    wait = rng.choice(("how long do they usually wait for the first of them", "how long until the first one, typically",
                       "how long does it usually take to get there"))
    if rng.random() < 0.3:
        out.append({"name": "max_days_to_next", "agg": "gap", "stat": "max", "from": f"{a}.date",
                    "to": [f"{n}.date" for n in names], "in": "days", "round": True})
        wait += ", and what is the longest wait"
        keep.cue("longest", r"longest|maximum|max|most")
    keep.cue("how long", r"how long|wait|until|time|take|delay")
    keep.cue("usually", USUAL)
    keep.forbid.append(NO_UNIT)
    keep.cue("groups", r"both|neither|none|nothing|combination|each|which|group|path|follow|next|after|go on")
    if len(nexts) == 2:
        options = f"{a_(nwords[0])}, {a_(nwords[1])}, both, or neither"
    else:
        options = f"{a_(nwords[0])} or not"
    lead = rng.choice((f"After a patient's first {awords[awords.index(' ') + 1:]}, what tends to follow: {options}",
                       f"Once a patient has {awords}, which comes next: {options}"))
    text = f"{lead}? How many patients take each path, and {wait}?"
    plan = {"route": "plan", "records": recs, "group": [{"after": a, "next": names}], "output": out,
            "order": [{"by": "patients", "desc": True}]}
    return plan, text, keep


def compare(ctx, rng):
    """Patients with a record whose code (or date) matches / matches none of another record's (cohort compare + has)."""
    keep = M.Keep()
    if rng.random() < 0.65:
        ta, tb = rng.choice(CODE_PAIRS)
        attr = "code"
    else:
        ta, tb = rng.choice(DATE_PAIRS)
        attr = "date"
    if rng.random() < 0.5:
        ta, tb = tb, ta
    if P.CURATED[ta].get(attr) is None or P.CURATED[tb].get(attr) is None:
        return None
    a, b = KINDS[ta][0], KINDS[tb][0]
    if a == b:
        return None
    op = rng.choice(("=", "!="))
    keep.cue(KINDS[ta][1], KINDS[ta][3])
    keep.cue(KINDS[tb][1], KINDS[tb][3])
    if attr == "code":
        words = (f"how many of our patients have {a_(KINDS[ta][1])} whose code also shows up among their {KINDS[tb][2]}"
                 if op == "=" else f"how many of our patients with {KINDS[tb][2]} have {a_(KINDS[ta][1])} whose code never shows up among them")
        keep.cue("code", r"code|same|match|also|never|none|not|appear|show")
    else:
        words = (f"how many of our patients have {a_(KINDS[ta][1])} dated on the date of one of their {KINDS[tb][2]}"
                 if op == "=" else f"how many of our patients with {KINDS[tb][2]} have {a_(KINDS[ta][1])} on a date when none of them took place")
        keep.cue("date", r"date|when|day")
    if op == "!=":
        keep.cue("not", r"\bnot\b|n't|\bnone\b|\bno\b|never|differ|other than|without|any of")
    cohort = {"and": [{"has": b}, {"compare": {"left": f"{a}.{attr}", "op": op, "right": f"{b}.{attr}"}}]}
    arec = {"type": ta}
    if attr == "date" and codes_of(ctx, ta) and rng.random() < 0.6:  # which record: one of its codes, by name if known
        arec["code"] = [rng.choice(codes_of(ctx, ta))]
        cw, named = code_words(ctx, rng, arec["code"][0], keep)
        one = KINDS[ta][1]
        words = words.replace(a_(one), f"{a_(one)} of {cw}" if named else f"{a_(one)} with {cw}", 1)
    plan = {"route": "plan", "records": {a: arec, b: {"type": tb}}, "cohort": cohort,
            "output": [{"name": "patients", "agg": "patients"}]}
    lead = rng.choice(("Roughly speaking, ", "In our data, ", "Out of curiosity, ", ""))
    text = lead + words + "?"
    return plan, text[0].upper() + text[1:], keep


SHAPES = [(breakdown, 74), (stay_split, 14), (pathway, 8), (compare, 6)]


# ---- no leak -------------------------------------------------------------------------------------------------------

def signature(plan):
    """(record types, grouping, outputs) of a plan, with record and output names, codes in conditions' records and
    ns / match left out: two plans with the same signature ask the same breakdown."""
    recs = plan.get("records") or {}
    typ = lambda n: (recs.get(n) or {}).get("type")
    types = tuple(sorted(str(r.get("type")) for r in recs.values()))

    def g(d):
        if "code_class" in d:
            rec, _, alias = d["code_class"].partition(".")
            return ("code_class", typ(rec), alias)
        if "stay_has" in d:
            return ("stay_has", typ(d["stay_has"]))
        if "after" in d:
            return ("after", typ(d["after"]), tuple(sorted(map(str, map(typ, d["next"])))))
        return ("sex",) if d.get("sex") else ("year", typ(str(d.get("year")).split(".")[0]))

    def o(m):
        c = m.get("if") or {}
        cond = ("has", typ(c["has"])) if "has" in c else ("stay_has", typ(c["stay_has"])) if "stay_has" in c else \
            (str(c.get("attr")).split(".")[-1], c.get("op"),
             None if isinstance(c.get("value"), (int, float)) and not isinstance(c.get("value"), bool) else str(c.get("value"))) \
            if c else None  # a threshold's number left out: another threshold asks the same breakdown
        of = str(m.get("of", ""))
        of = of.split(".")[-1] if "." in of else typ(of) if of else None
        return (m["agg"], of, cond, m.get("stat"), m.get("in"))
    return types, tuple(g(d) for d in plan.get("group") or []), frozenset(o(m) for m in plan.get("output") or [])


def org_plans():
    """The organizers' right plans (resources/plans.jsonl org-*, route plan only)."""
    out = []
    for line in (P.HERE / "resources" / "plans.jsonl").read_text().splitlines():
        d = json.loads(line)
        if d["id"].startswith("org-") and d["plan"].get("route") == "plan":
            out.append(d["plan"])
    return out


def leaks(plan, orgs):
    """Why a plan is too close to an organizers' right plan (None when it is not): equal to one, the same record types
    and grouping with the same outputs or all but one, O02 (drug administrations by stay_has a billed diagnosis with
    hours), O04 (a birth's country compared with a nationality's), O01 (a cancer diagnosis, then surgery or radiotherapy)."""
    canon, sig = P._canonical(plan), signature(plan)
    for org in orgs:
        if canon == P._canonical(org):
            return "equal to an organizers' plan"
        types, groups, outs = signature(org)
        if sig[0] == types and sig[1] == groups and (sig[2] == outs or len(sig[2] & outs) >= len(outs) - 1):
            return "the same record types, grouping and outputs as an organizers' plan"
    recs = plan.get("records") or {}
    typ = {n: r.get("type") for n, r in recs.items()}
    stays = [d["stay_has"] for d in plan.get("group") or [] if "stay_has" in d] + \
        [m["if"]["stay_has"] for m in plan.get("output") or [] if "stay_has" in (m.get("if") or {})]
    if "DrugAdministrationEvent" in typ.values() and any(typ.get(s) == "BilledDiagnosis" for s in stays) and \
            any(str(m.get("of", "")).endswith(".hours") for m in plan.get("output") or []):
        return "O02"
    if {"Birth", "Nationality"} <= set(typ.values()):
        return "O04"
    if O01_TYPES <= set(typ.values()):
        return "O01"
    if any(c.startswith("icd-10-gm:A00") or c.startswith("chop:00.0") for r in recs.values() for c in r.get("code") or []):
        return "an official question's code"
    return None


def conventions(plan):
    """Which convention a plan breaks ([] when none)."""
    bad, out = [], plan.get("output") or []
    for m in out:
        if m["agg"] in ("avg", "min", "max", "sum") and str(m.get("of", "")).endswith(".days"):
            bad.append("a duration is in hours")
        if m["agg"] == "avg" and not m.get("round"):
            bad.append("an average is rounded")
        if m["agg"] == "gap" and (m.get("in") != "days" or not m.get("round")):
            bad.append("a gap is in days, rounded")
    if plan.get("group"):
        counts = [m["name"] for m in out if m["agg"] in ("patients", "records")]
        if not counts or plan.get("order") != [{"by": counts[0], "desc": True}]:
            bad.append("a breakdown is ordered by its first count, largest first")
    return bad


def sample(n, seed, ctx, prefix="org", seen=None):
    """n plans {id, shape, plan, template, keep}, none twice (seen: canonical plans already taken), none an organizers'."""
    rng, items, orgs = random.Random(seed), [], org_plans()
    seen = set() if seen is None else seen
    tries = 0
    while len(items) < n and tries < n * 60:
        tries += 1
        maker = weighted(rng, [(f, w) for f, w in SHAPES])
        got = maker(ctx, rng)
        if got is None:
            continue
        plan, text, keep = got
        canon = P._canonical(plan)
        if canon in seen or P.check(plan) or leaks(plan, orgs) or conventions(plan):
            continue
        try:
            P.compile(plan)
        except P.Unsupported:
            continue
        seen.add(canon)
        items.append({"id": f"{prefix}{len(items):05d}", "shape": maker.__name__, "plan": plan, "template": text,
                      "keep": keep.dump()})
    return items


def held():
    """The official questions (tests/bench/official.jsonl): no training question may be like one."""
    path = P.HERE.parents[1] / "tests" / "bench" / "official.jsonl"
    return [json.loads(line)["question"] for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def near(text, questions, threshold=0.45):
    from ...translation.prompt import tok
    x = set(tok(text))
    return any(x and len(x & set(tok(q))) / len(x | set(tok(q))) >= threshold for q in questions)


def reword_all(items, cache, log):
    """{id: [rewrites that pass problems()]}: two rewrites per plan in the organizers' voice, a second try for a plan
    with fewer than two."""
    t0 = time.time()
    got, retried = M.reword_items(items, cache, M.PAIRS_ORGANIZERS, WORKERS)
    log(f"rewrites: {retried} plans retried, {time.time() - t0:.0f} s")
    return {it["id"]: list(dict.fromkeys(x for _, x in got[it["id"]] if not M.problems(x, it)))[:2] for it in items}


def rows(items, texts, official):
    """Training rows: the kept rewrites, plus the template for ~20% of the plans and for a plan with no kept rewrite;
    none like an official question."""
    rng, out = random.Random(7), []
    for it in items:
        qs = list(texts.get(it["id"], []))
        if not qs or rng.random() < M.TEMPLATE_SHARE:
            qs.append(it["template"])
        for q in qs:
            if near(q, official):
                continue
            out.append(M.row(q, it["plan"]))
    return out


def context():
    """The sampler's codes, plain names and coded details' values from the inventory and training.make's caches (no
    database query, no model call)."""
    inv, source = M.inventory()
    known = M.Cache(M.CACHE / "known_codes.jsonl").data
    pool = M.code_pool(inv, known)
    dvals = M.detail_values(known)
    return Ctx(pool, cached_names(pool), dvals), source


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plans", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--plain", type=int, default=1000, help="more plans whose templates name no code")
    ap.add_argument("--no-llm", action="store_true", help="templates only, as the questions (no rewording)")
    args = ap.parse_args()
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.0f} s] {m}", flush=True)
    ctx, source = context()
    seen = set()
    items = sample(args.plans, args.seed, ctx, seen=seen)
    if args.plain:  # more plans whose templates name no code (plain names and kinds only)
        items += sample(args.plain, args.seed + 100, Ctx(ctx.pool, ctx.names, ctx.dvals, plain=True), "orgp", seen)
    shapes = {}
    for it in items:
        shapes[it["shape"]] = shapes.get(it["shape"], 0) + 1
    log(f"{len(items)} plans (codes from {source}, {len(ctx.names)} plain names): {shapes}")
    if args.no_llm:
        texts = {it["id"]: [] for it in items}
    else:
        M.wake()
        texts = reword_all(items, M.Cache(M.CACHE / "rewrites_orgstyle.jsonl"), log)
    official = held()
    rng = random.Random(args.seed + 1)
    order = items[:]
    rng.shuffle(order)
    n_val = max(1, round(M.VAL_SHARE * len(order)))
    for k, part in (("val", order[:n_val]), ("train", order[n_val:])):
        lines = rows(part, texts, official)
        rng.shuffle(lines)
        path = M.OUT / f"{k}_orgstyle.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines))
        log(f"{path}: {len(lines)} questions from {len(part)} plans")
    log(f"plans without a kept rewrite: {sum(not texts.get(it['id']) for it in items)}")


if __name__ == "__main__":
    main()
