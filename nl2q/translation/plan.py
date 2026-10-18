"""The plan path: the generator writes a short JSON plan of the request (vLLM structured output, SCHEMA); plain code
checks it (check) and compiles it (compile) into the SPHN team's fast query pattern (docs/official/sphn-sparql-
optimization): one sub-query per record inside GRAPH ?g, a yes/no flag per record, GROUP BY the patient, a boolean
FILTER, then the count. The code triple comes first, with OPTION (inference ...) on it only; finish() adds
DEFINE sql:select-option "order", so the written order is the execution order.
Unsafe parts of a request stay literal (field `unsafe`, one entry per kind of Query_Compliance.md), so the compliance
layer judges the query the request really asks for. A request a plan cannot express says route "free" and goes to
free generation.

A plan, e.g. "How many patients have both a billed diagnosis E11 and a lab test 718-7?":
  {"route": "plan",
   "records": {"dx": {"type": "BilledDiagnosis", "code": ["icd-10-gm:E11"]},
               "lab": {"type": "LabTestEvent", "code": ["loinc:718-7"]}},
   "cohort": {"and": [{"has": "dx"}, {"has": "lab"}]},
   "output": [{"name": "countResult", "agg": "patients"}]}
What a plan can say: README.md here (and resources/plan.md, the plan writer's instructions)."""
import json, re
from functools import lru_cache
from pathlib import Path
from .. import schema as SC
from .. import settings as S

HERE = Path(__file__).parent
HAS_CODE = "sphn:hasCode"
RESULT = "sphn:hasResult/sphn:hasQuantity"
HIERARCHY = {"icd-10-gm", "chop", "snomed", "atc", "sphn-atc", "icd-o-3"}  # code systems with sub-codes: inference
ONE_PATIENT = S.GRAPH_PREFIX  # a patient's graph: GRAPH_PREFIX + its file name

# Record type -> how its parts are reached (all paths SPHN 2026.1). code: path from the record (or from `via`) to its
# code node; via: the node that holds code and result (a lab test event's lab test); typed: whether the record is
# typed when it has a code; date, value (path to the quantity), age: what a condition may compare; systems: the code
# systems its codes come from. Sources: docs/official/sphn-sparql-optimization AND.rq / BEFORE.rq (diagnosis: code,
# record date; lab test: hasLabTest, report date), Lab Value Restriction.rq (lab result quantity), Medication (SNOMED
# CT).rq (drug: hasDrug/hasActiveIngredient/hasCode), StepD.rq (administrative sex, no inference), the 2026.1 schema
# (properties of each record type, hasSubjectAge), conventions.md (sex codes, stay dates).
DIAGNOSIS = {"code": HAS_CODE, "date": "sphn:hasRecordDateTime", "age": True, "systems": ("icd-10-gm",), "case": True}
SPAN = {"start": "sphn:hasStartDateTime", "end": "sphn:hasEndDateTime", "case": True}  # durations; stay through the case
PROCEDURE = {"code": HAS_CODE, "date": "sphn:hasStartDateTime", "systems": ("chop", "snomed"), **SPAN}
MEASUREMENT = {"code": HAS_CODE, "date": "sphn:hasStartDateTime", "value": RESULT, "systems": ("loinc", "snomed"), **SPAN}
# v2 attributes (organizers' examples 3, 6, 7; 2026.1 schema): rank (principal = snomed:63161005, conventions.md),
# route (a drug administration's route code), in_stay (the record starts between its stay's admission and discharge).
RANK = {"rank": "sphn:hasRankCode"}
RECORDS = {
    "BilledDiagnosis": {**DIAGNOSIS, **RANK},
    "NursingDiagnosis": {**DIAGNOSIS, "systems": ("icd-10-gm", "snomed")},
    "OncologyDiagnosis": {**DIAGNOSIS, "systems": ("icd-10-gm", "icd-o-3", "snomed")},
    "ProblemCondition": {"code": HAS_CODE, "date": "sphn:hasRecordDateTime", "systems": ("icd-10-gm", "snomed"), "case": True},
    "LabTestEvent": {"via": "sphn:hasLabTest", "code": HAS_CODE, "typed": False, "date": "sphn:hasReportDateTime",
                     "value": RESULT, "systems": ("loinc", "snomed"), "case": True},
    "DrugAdministrationEvent": {"code": "sphn:hasDrug/sphn:hasActiveIngredient/sphn:hasCode",
                                "date": "sphn:hasStartDateTime", "value": "sphn:hasQuantity",
                                "systems": ("snomed", "atc", "sphn-atc"), **SPAN,
                                "route": "sphn:hasAdministrationRouteCode"},
    "AdministrativeSex": {"code": HAS_CODE, "inference": False, "systems": ("snomed",)},
    "BilledProcedure": {**PROCEDURE, **RANK},
    "MedicalProcedure": {**PROCEDURE, "age": True, **RANK},
    "OncologySurgery": PROCEDURE,
    "RadiotherapyProcedure": PROCEDURE,
    # data inventory (nl2q_data/inventory, 5k patients) and 2026.1 schema: an allergy's code is its allergen's, a
    # death's its condition's; these date properties carry data and are 2026.1 terms too
    "Allergy": {"code": "sphn:hasAllergen/sphn:hasCode", "date": "sphn:hasFirstRecordDateTime", "systems": ("snomed",)},
    "AdverseEvent": {"code": HAS_CODE, "date": "sphn:hasOnsetDateTime", "systems": ("snomed",)},
    "Death": {"code": "sphn:hasConditionCode", "date": "sphn:hasReportDateTime", "systems": ("icd-10-gm", "snomed")},
    "AdministrativeCase": {"date": "sphn:hasAdmission/sphn:hasDateTime", "systems": ()},
    "BodyTemperatureMeasurement": MEASUREMENT,
    "HeartRateMeasurement": MEASUREMENT,
    "BloodPressureMeasurement": {k: v for k, v in MEASUREMENT.items() if k != "value"},  # systolic/diastolic, no quantity
    "BodyWeightMeasurement": MEASUREMENT,
    "BodyHeightMeasurement": MEASUREMENT,
    "OxygenSaturationMeasurement": MEASUREMENT,
    "RespiratoryRateMeasurement": MEASUREMENT,
    "BodyMassIndex": {"date": "sphn:hasDeterminationDateTime", "value": "sphn:hasQuantity", "systems": ()},
    "LabResult": {"code": HAS_CODE, "value": "sphn:hasQuantity", "systems": ("loinc",)},
    # organizers' examples 8 (genetic variants) and 10 (births): grouped by their coded details
    "VariantDescriptor": {"code": HAS_CODE, "systems": ("so", "geno", "snomed")},
    "Birth": {"systems": (), "country": "sphn:hasCountry/sphn:hasCode"},
    # organizers' example 4 (2026.1 schema: Birth -> hasCountry, Nationality -> hasAssociatedCountry -> Country ->
    # hasCode): a country is compared with another record's country (cohort "compare")
    "Nationality": {"systems": (), "country": "sphn:hasAssociatedCountry/sphn:hasCode"},
}
CURATED = dict(RECORDS)  # the hand-written specs above: they always win, in this order
# Every other SPHN 2026.1 record type with data, and every schema property with data the curated tables do not cover
# (resources/schema_records.json, python -m nl2q.tools.schema_records): the generic types follow the curated ones
# (alphabetical), with the same spec keys; GENERIC holds each type's generic details {alias: (path, kind)}. Static: only
# this file is read, never the database.
SCHEMA_RECORDS = json.loads((HERE / "resources" / "schema_records.json").read_text())
RECORDS.update({t: {**e["spec"], "systems": tuple(e["spec"]["systems"])} for t, e in SCHEMA_RECORDS.items()
                if "spec" in e and t not in CURATED})
GENERIC = {t: {a: tuple(v) for a, v in e["details"].items()} for t, e in SCHEMA_RECORDS.items()}
UNSAFE_FORMS =("DESCRIBE", "CONSTRUCT", "ASK", "DELETE", "INSERT")
OPS = ("=", "!=", "<", "<=", ">", ">=")
ALIASES = ("date", "value", "unit", "age", "start", "end", "hours", "days", "rank", "route", "in_stay")
NUMERIC = ("value", "age", "hours", "days")
CODE_ALIASES = ("unit", "rank", "route")  # compared with a code ("main" for the principal rank)
# Each record type's other coded details (resources/coded.json, python -m nl2q.tools.coded_details): {alias: [property,
# "code" (grouped by the code's class) or "value" (a value-set individual)]}; the aliases above keep their meaning.
CODED = json.loads((HERE / "resources" / "coded.json").read_text())


def coded(type_):
    """The coded details of a record type beyond its fixed attributes: {alias: (property, kind)}; a detail that is the
    record's own code or a fixed attribute under another name (a drug's administration_route = route, a death's
    condition = its code) is left out, so a detail has one spelling."""
    spec = RECORDS.get(type_, {})
    taken = {spec.get(k) for k in ("code", *CODE_ALIASES)} - {None}
    out = {a: tuple(v[:2]) for a, v in CODED.get(type_, {}).items() if a not in ALIASES and v[0] not in taken}
    return out | {a: v[:2] for a, v in GENERIC.get(type_, {}).items() if v[1] in ("code", "value") and a not in out}


# Each record type's other useful attributes in the SPHN 2026.1 schema (tests/translation/test_plan_attrs.py checks every
# path against the schema file): {alias: (path, kind)}. kind "number": path to a quantity, compared by its value, its
# unit is "<alias>_unit" (a ucum: code); "date": a date-time literal. Organizers' example 10 (gestational age in days:
# Birth -> hasGestationalAge -> GestationalAgeAtBirth -> hasQuantity); the others carry data in the 5k-patient inventory.
MEASURES = {
    "Birth": {"gestational_age": ("sphn:hasGestationalAge/sphn:hasQuantity", "number")},
    "DrugAdministrationEvent": {"duration": ("sphn:hasDuration", "number")},
    "RadiotherapyProcedure": {"fractions": ("sphn:hasFractionsNumber", "number"),
                              "radiation_quantity": ("sphn:hasRadiationQuantity", "number")},
    "LabResult": {"time_to_positivity": ("sphn:hasTimeToPositivity", "number")},
    "OncologyDiagnosis": {"incidence_date": ("sphn:hasIncidenceDateTime", "date")},
    "ProblemCondition": {"onset_date": ("sphn:hasOnsetDateTime", "date")},
    "Allergy": {"last_reaction_date": ("sphn:hasLastReactionDateTime", "date")},
}
# The insurance of a record's stay (organizers' example 12): the coverage type (a value-set individual: General,
# Private, ...) of the InsuranceStatus on the same AdministrativeCase (2026.1: InsuranceStatus -> hasAdministrativeCase,
# hasCoverageType). Every record type with a stay link has it, and the stay itself.
INSURANCE = ("sphn:hasCoverageType", "insurance")


def extra(type_):
    """A record type's schema attributes beyond the fixed and coded ones: {alias: (path, kind)}, units included
    ("<alias>_unit": (path to the quantity, "unit"))."""
    spec, out = RECORDS.get(type_, {}), {}
    generic = [(a, v[:2]) for a, v in GENERIC.get(type_, {}).items() if v[1] in ("number", "date")]
    for a, (path, kind) in [*MEASURES.get(type_, {}).items(), *generic]:
        out[a] = (path, kind)
        if kind == "number":
            out[a + "_unit"] = (path, "unit")
    if spec.get("case") or type_ == "AdministrativeCase":
        out["insurance"] = INSURANCE
    return out


def attr_kind(type_, alias):
    """What an attribute of a record type is: "number", "date", "unit", "code", "value" (a value-set individual, the
    insurance too) or None (not one of the schema attributes above, nor a coded detail)."""
    if alias in extra(type_):
        k = extra(type_)[alias][1]
        return "value" if k == "insurance" else k
    return coded(type_).get(alias, (None, None))[1]


MAIN = "snomed:63161005"  # principal rank (conventions.md; organizers' examples 3, 6)
V2_AGGS = ("avg", "min", "max", "sum", "share", "patients_if")
DURATION = r"^P(?=\d)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?$"
NAME = r"^[a-z][a-z0-9_]{0,23}$"
CODE_FORM = r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._-]*$"  # prefix:code
SEX = (("snomed:248153007", "male"), ("snomed:248152002", "female"), ("snomed:32570681000036106", "indeterminate"))


def has_alias(spec, alias):
    """Whether a record type has an attribute: stored (date, value, start, ...) or computed (hours, days, in_stay)."""
    need = {"unit": ("value",), "hours": ("start", "end"), "days": ("start", "end"), "in_stay": ("start", "case")}
    return all(spec.get(k) for k in need.get(alias, (alias,)))


# The JSON schema the generator's output must follow (vLLM structured_outputs {"json": SCHEMA}).
_name = {"type": "string", "pattern": NAME}
_attr = {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,23}\.[a-z][a-z_]{0,30}$"}  # check() knows each type's attributes
_test = {"type": "object", "properties": {"attr": _attr, "op": {"enum": list(OPS)},
                                          "value": {"type": ["number", "string", "boolean"]}},
         "required": ["attr", "op", "value"], "additionalProperties": False}
_has = {"type": "object", "properties": {"has": _name}, "required": ["has"], "additionalProperties": False}
_stay_has = {"type": "object", "properties": {"stay_has": _name}, "required": ["stay_has"], "additionalProperties": False}
GAP_STATS = ("avg", "min", "max")
SCHEMA = {
    "$defs": {"cond": {"anyOf": [
        _has,
        {"type": "object", "properties": {"and": {"type": "array", "items": {"$ref": "#/$defs/cond"}, "minItems": 2}},
         "required": ["and"], "additionalProperties": False},
        {"type": "object", "properties": {"or": {"type": "array", "items": {"$ref": "#/$defs/cond"}, "minItems": 2}},
         "required": ["or"], "additionalProperties": False},
        {"type": "object", "properties": {"not": {"$ref": "#/$defs/cond"}}, "required": ["not"], "additionalProperties": False},
        {"type": "object", "properties": {"before": {"type": "object", "properties": {
            "first": _attr, "then": _attr, "gap": {"type": "string", "pattern": DURATION}},
            "required": ["first", "then"], "additionalProperties": False}}, "required": ["before"], "additionalProperties": False},
        {"type": "object", "properties": {"same_stay": {"type": "array", "items": _name, "minItems": 2, "maxItems": 2}},
         "required": ["same_stay"], "additionalProperties": False},
        {"type": "object", "properties": {"compare": {"type": "object", "properties": {
            "left": _attr, "op": {"enum": ["=", "!="]}, "right": _attr}, "required": ["left", "op", "right"],
            "additionalProperties": False}}, "required": ["compare"], "additionalProperties": False}]}},
    "type": "object",
    "properties": {
        "route": {"enum": ["plan", "free"]},
        "records": {"type": "object", "additionalProperties": {"type": "object", "properties": {
            "type": {"enum": list(RECORDS)},
            "code": {"type": "array", "items": {"type": "string", "pattern": CODE_FORM}, "minItems": 1},
            "where": {"type": "array", "items": _test},
            "identifier": {"type": "string"}},
            "required": ["type"], "additionalProperties": False}},
        "cohort": {"$ref": "#/$defs/cond"},
        "group": {"type": "array", "minItems": 1, "items": {"anyOf": [
            {"type": "object", "properties": {"code_class": {"type": "string"}, "ns": {"type": "string"}, "match": {"type": "string"}},
             "required": ["code_class"], "additionalProperties": False},
            {"type": "object", "properties": {"sex": {"type": "boolean"}}, "required": ["sex"], "additionalProperties": False},
            {"type": "object", "properties": {"year": _attr}, "required": ["year"], "additionalProperties": False},
            _stay_has,
            {"type": "object", "properties": {"after": _name, "next": {"type": "array", "items": _name, "minItems": 1}},
             "required": ["after", "next"], "additionalProperties": False}]}},
        "output": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
            "name": _name, "agg": {"enum": ["patients", "records", "first", "last", *V2_AGGS, "gap"]}, "of": {"type": "string"},
            "if": {"anyOf": [_test, _has, _stay_has]},
            # the training data's key order (structured outputs keep this order: "stat" before "from" made gaps unwritable)
            "stat": {"enum": list(GAP_STATS)}, "from": _attr, "to": {"type": "array", "items": _attr, "minItems": 1},
            "in": {"enum": ["days", "hours"]}, "round": {"type": "boolean"}},
            "required": ["name", "agg"], "additionalProperties": False}},
        "order": {"type": "array", "items": {"type": "object", "properties": {"by": _name, "desc": {"type": "boolean"}},
                                             "required": ["by"], "additionalProperties": False}},
        "limit": {"type": "integer"},
        "unsafe": {"type": "object", "properties": {
            "expose": {"enum": ["list", "rows"]}, "per_patient": {"type": "boolean"},
            "rows": {"type": "array", "items": _attr, "minItems": 1}, "form": {"enum": list(UNSAFE_FORMS)},
            "whole": {"enum": ["triples", "types"]}, "graph": {"type": "string"}, "service": {"type": "string"},
            "other": {"type": "string"}},  # graph and service last, as in the training data
            "additionalProperties": False}},
    "required": ["route"],
    "additionalProperties": False,
}


def catalog():
    """One line per record type: the code systems its codes come from and the attributes a condition may use."""
    return "\n".join(f"- {t} ({', '.join(s['systems']) or ('any code' if s.get('code') else 'no code')}; "
                     f"{', '.join([a for a in ALIASES if has_alias(s, a)] + list(coded(t)) + list(extra(t))) or 'no attributes'})"
                     for t, s in RECORDS.items())


INSTRUCTIONS = (HERE / "resources" / "plan.md").read_text().strip() + "\n" + catalog()
# The only instruction a model fine-tuned on plans gets (it learned the rest): its training data uses the same line.
TRAINED = 'Write the JSON plan of this request about SPHN clinical data. Keep unsafe parts literal. {"route":"free"} when a plan cannot say it.'


VARIANTS = {"r1": {"graph": "gen_mock_data-1_1000001"}, "r4": {"form": "DESCRIBE"}, "r5": {"expose": "list"}}


def examples(skip=()):
    """[(question, plan, family)] of the plan examples (resources/plans.jsonl; questions from config/examples.md),
    plus the unsafe variants of the template families (as make_examples makes them), without the ids in skip."""
    from .examples import load
    by_id = {e["id"]: e for e in load()}
    out = []
    for line in (HERE / "resources" / "plans.jsonl").read_text().splitlines():
        p = json.loads(line)
        for id_, plan in [(p["id"], p["plan"])] + [(f"{p['id']}-{k}", {**p["plan"], "unsafe": u}) for k, u in VARIANTS.items()]:
            if id_ in by_id and id_ not in skip and by_id[id_]["family"] not in skip:
                out.append((by_id[id_]["question"], plan, by_id[id_]["family"]))
    return out


@lru_cache(1)
def _index():
    from rank_bm25 import BM25Okapi
    from .prompt import tok
    pool = examples()
    return pool, BM25Okapi([tok(q) for q, _, _ in pool])


def kind(plan):
    """free, grouped (v2), unsafe or count: the shots always show one of each, so the model sees every route."""
    if plan.get("route") != "plan":
        return "free"
    if _grouped(plan):
        return "grouped"
    return "unsafe" if plan.get("unsafe") else "count"


def shots(text, k=None):
    """[(question, plan)]: the k plan examples most like the request (BM25, as prompt.shots), at least one of each
    kind (the most similar of its kind replaces the least similar shot), the most similar last. With S.HOLDOUT
    (evaluation) the request's own example and its family are never shown."""
    from .prompt import _same, tok
    k = S.PLAN_SHOTS if k is None else k
    pool, bm = _index()
    hidden = {f for q, _, f in pool if _same(text, q)} if S.HOLDOUT else set()
    scores = bm.get_scores(tok(text))
    ranked = [pool[i] for i in sorted(range(len(pool)), key=lambda i: -scores[i]) if pool[i][2] not in hidden]
    chosen = ranked[:k]
    for want in ("free", "grouped", "unsafe", "count"):
        if k >= 4 and not any(kind(p) == want for _, p, _ in chosen):
            best = next((x for x in ranked if kind(x[1]) == want), None)
            if best is not None:
                drop = next(i for i in range(len(chosen) - 1, -1, -1)
                            if sum(kind(c[1]) == kind(chosen[i][1]) for c in chosen) > 1)
                chosen[drop] = best
    order = {id(x): i for i, x in enumerate(ranked)}
    return [(q, p) for q, p, _ in sorted(chosen, key=lambda x: order[id(x)])][::-1]


def messages(text, shots=None):
    """Chat messages asking for the plan of a request; shots: [(question, plan)] shown as worked examples."""
    turns = []
    for q, p in shots or []:
        turns += [{"role": "user", "content": q}, {"role": "assistant", "content": json.dumps(p, separators=(",", ":"))}]
    return [{"role": "system", "content": INSTRUCTIONS}, *turns, {"role": "user", "content": text}]


def _canonical(plan):
    """The plan with record names and output names left out of the comparison: two plans that say the same thing
    (records "dx" and "diag", counts "n" and "patients") compare equal (the training-data tools drop duplicates)."""
    recs = plan.get("records") or {}
    order = sorted(recs, key=lambda n: json.dumps(recs[n].get("type")) + json.dumps(recs[n].get("code")))
    text = json.dumps({**plan, "output": [{**m, "name": ""} for m in plan.get("output") or []]}, sort_keys=True)
    for i, n in enumerate(order):
        text = re.sub(rf'"{re.escape(n)}(?=[".])', f'"r{i}', text)
    return text


def write(text, usage, llm_fn=None):
    """(plan, None) or (None, why the request goes to free generation). The plan writer is the generator with
    instructions and similar examples (the fallback when no fine-tuned model is served), or a model fine-tuned on plans
    (S.PLAN_MODEL) with its one-line instruction. A plan check() rejects is retried once with what is wrong."""
    from .llm import llm
    llm_fn = llm_fn or llm
    trained = S.PLAN_MODEL != S.LLM_MODEL
    msgs = ([{"role": "system", "content": TRAINED}, {"role": "user", "content": text}] if trained else
            messages(text, shots(text)))
    problems = []
    for _ in range(2):
        try:
            plan = json.loads(llm_fn(msgs, usage, schema=SCHEMA, url=S.PLAN_URL, model=S.PLAN_MODEL))
        except ValueError:
            return None, "plan: the answer was not JSON"
        if plan.get("route") != "plan" or (plan.get("unsafe") or {}).get("other"):
            return None, "plan: free"
        problems = check(plan) or type_problems(text, plan)
        if not problems:
            return plan, None
        msgs = msgs + [{"role": "assistant", "content": json.dumps(plan, separators=(",", ":"))},
                       {"role": "user", "content": "Fix the plan: " + "; ".join(problems)}]
    return None, "plan: " + "; ".join(problems)


# Record types a request must announce, and what to write otherwise: the 9B wrote OncologyDiagnosis for cholera
# (ICD-10-GM A00). An ICD-10-GM neoplasm code (C00-D48) or an ICD-O-3 code announces cancer by itself.
NEEDS = {
    "OncologyDiagnosis": (r"cancer|oncolog|tumou?r|neoplas|carcinom|malignan|lymphom|leuk[a]?emi|sarcom|metasta",
                          "a diagnosis without cancer is a BilledDiagnosis"),
    "OncologySurgery": (r"cancer|oncolog|tumou?r|neoplas|carcinom|malignan|surg|resection|operat",
                        "a procedure is a BilledProcedure or a MedicalProcedure"),
    "RadiotherapyProcedure": (r"radio|radiation|irradiat|session", "a procedure is a BilledProcedure or a MedicalProcedure"),
    "NursingDiagnosis": (r"nurs", "a diagnosis is a BilledDiagnosis"),
    "ProblemCondition": (r"problem|condition", "a diagnosis is a BilledDiagnosis"),
}
CANCER_CODE = re.compile(r"^(?:icd-10-gm:(?:C|D[0-4])|icd-o-3:)", re.I)


# Words of a generic type's name too common to announce it (a "lab test event" is a LabTestEvent, curated)
PLAIN_WORDS = {"event", "lab", "test", "result", "measurement", "presence", "procedure", "data", "file", "status", "body"}


def announced(t, text):
    """Whether a request names a generic record type: a distinctive word of its name, by its first five letters
    (DrugPrescription: "drug" or "presc..."; TumorSpecimen: "tumor" or "speci..."), else its whole name."""
    words = [w.lower() for w in re.findall(r"[A-Z][a-z]+", t)]
    stems = [w[:5] for w in words if w not in PLAIN_WORDS] or [" ".join(words)]
    return any(re.search(rf"\b{re.escape(w)}", text, re.I) for w in stems)


def type_problems(text, plan):
    """The plan's records of a type the request does not announce (none on the 361 right plans of the validation set);
    a generic type (beyond the curated ones) must be named by the request."""
    out = []
    for name, r in (plan.get("records") or {}).items():
        t = r.get("type")
        if t in NEEDS and not re.search(NEEDS[t][0], text, re.I) and not (
                t.startswith("Oncology") and any(CANCER_CODE.match(c) for c in r.get("code") or [])):
            out.append(f"{name}: the request does not ask for a {t}; {NEEDS[t][1]}")
        elif t in RECORDS and t not in CURATED and not announced(t, text):
            out.append(f"{name}: the request does not ask for a {t}")
    return out


# Words that announce each unsafe part of a request, from Query_Compliance.md and the organizers' non-compliant
# examples (11-15): a plan's unsafe field must be announced by the request, and a request that announces one must get it.
CUES = {
    "graph": r"\bgraph\b|mock_data|\bpatient\s+(?:no\.?\s*|number\s*|#\s*)?\d",
    "identifier": r"\bidentifier\b|\bID[-\s]?\d|\bid\s+\w*\d",
    "expose": r"identifier|\bids?\b|\bwhich (?:ones|patients)\b|\bwho (?:are|they)\b|\bname them\b|\blist (?:the |all )?(?:patients|ids)",
    "per_patient": r"\b(?:for|per) (?:each|every) patient\b|\bper patient\b|\beach patient\b",
    "rows": r"\beach (?:case|record|row|one)\b|\bshow me (?:each|every|all)\b|\blist (?:each|every|all)\b|\braw\b|\bone row\b",
    "DESCRIBE": r"\bdescribe\b|\ball (?:the )?(?:data|information) (?:of|about)\b|\beverything about\b",
    "CONSTRUCT": r"\bconstruct\b|\bas a graph\b|\bexport\b|\btake away\b|\bdownload\b|\bcopy of\b",
    "ASK": r"^\s*(?:is|are|does|do|was|were|has|have|can)\b|\bwhether\b|\bis there\b|\bare there\b",
    "DELETE": r"\bdelete\b|\bremove\b|\berase\b|\bdrop\b|\bwipe\b|\bclear\b",
    "INSERT": r"\binsert\b|\badd (?:a |new )?(?:record|row|triple|data|copy)\b|\bcopy\b|\bduplicate\b",
    "service": r"https?://|\bendpoint\b|\bremote\b",
    "whole": r"\btriples?\b|\ball (?:the )?data\b|\bhow much data\b|\beverything\b|\beach (?:type|kind) of (?:record|data)\b|\brecords of each type\b",
}
# Cues that never come without an unsafe part, and the plan fields that may carry them.
STRONG = {"another endpoint": (r"https?://", {"service"}),
          "deletion": (r"\bdelete\b|\berase\b|\bwipe\b", {"DELETE"}),
          "one patient": (r"mock_data|\bgraph\s+<?\S*\d|\bpatient\s+(?:no\.?\s*|number\s*|#\s*)?\d{2,}|\bID-\d",
                          {"graph", "identifier", "expose"})}


def cue_mismatch(text, plan):
    """Why a plan's unsafe parts and the request disagree, or None: an unsafe field the request does not announce
    (the model invented it), or a strong unsafe cue the plan lacks (it dropped one). Either way the request goes to
    free generation, which translates it literally: this never makes an unsafe request look safe."""
    u = plan.get("unsafe") or {}
    parts = [k for k in ("graph", "expose", "per_patient", "rows", "service", "whole") if u.get(k)]
    parts += [u["form"]] if u.get("form") else []
    parts += ["identifier"] if any(r.get("identifier") for r in (plan.get("records") or {}).values()) else []
    for p in parts:
        if not re.search(CUES[p], text, re.I):
            return f"the plan says {p}, the request does not"
    for what, (rx, fields) in STRONG.items():
        if re.search(rx, text, re.I) and not fields & set(parts):
            return f"the request names {what}, the plan does not"
    return None


# Words that announce what no plan can say: a plan written for such a request silently drops that part (the 9B wrote
# "at least 7 days before" for "within 7 days after", a date range for "per month"), so it goes to free generation.
BEYOND = {
    "a time window": r"\bwithin\s+(?:\d+|a|an|one|two|three|four|five|six|seven|ten|twelve|thirty)\s+(?:hours?|days?|weeks?|months?|years?)\b",
    "the same day": r"\bsame\s+(?:day|date)\b",  # the same stay is a plan: same_stay, stay_has
    "months, weeks or days": r"\b(?:per|each|every|by)\s+(?:month|week|quarter|day)\b|"  # not "our quarterly report"
                             r"\b(?:monthly|weekly|quarterly|daily)\s+(?:counts?|numbers?|totals?|breakdown|trends?|figures)\b",
    "bins": r"\bbins?\b|\bbuckets?\b|\bhistogram\b|\bintervals?\s+of\b",
    "a median or a spread": r"\bmedian\b|\bpercentiles?\b|\bquartiles?\b|\bstandard deviation\b|\bvariance\b",
    # each patient's records counted, then averaged: per_patient lists the counts, no output averages them
    "an average count per patient":
        r"\b(?:average|mean)\s+(?:number|count)\s+of\b[^?]{0,200}?\b(?:per|for\s+each|for\s+every)\s+(?:patient|subject)\b|"
        r"\b(?:average|mean)\s+(?:[a-z-]+\s+){0,5}?[a-z-]+s\s+per\s+(?:patient|subject)\b|"  # average lab tests per patient
        r"\bon\s+average\b[^?]{0,40}?\bhow\s+many\s+(?!(?:days|hours|weeks|months|years|minutes)\b)[^?]{0,150}?"
        r"\b(?:per|(?:does|do|did)\s+(?:a|each|every|one))\s+(?:patient|subject)\b(?!['’]s)|"
        r"\bhow\s+many\s+(?!(?:days|hours|weeks|months|years|minutes)\b)[^?]{0,150}?"
        r"\b(?:per|(?:does|do|did)\s+(?:a|each|every|one))\s+(?:patient|subject)\b(?!['’]s)[^?]{0,150}?\bon\s+average\b",
    # identifiers counted (not patients): the plan lists them instead ("how many patients ..., and their ids" is a list)
    "a count of patient identifiers":
        r"\bhow\s+many\s+(?:distinct\s+|unique\s+|different\s+)?(?:patient|subject)s?['’]?\s+(?:pseudo\W?)?(?:identifiers|ids)\b|"
        r"\b(?:number|count)\s+of\s+(?:the\s+)?(?:distinct\s+|unique\s+|different\s+)?(?:patient\s+|subject\s+)?pseudo\W?(?:identifiers|ids)\b|"
        r"\bcount\s+(?:the\s+|all\s+)?(?:distinct\s+|unique\s+|different\s+)?(?:patient\s+|subject\s+)?pseudo\W?(?:identifiers|ids)\b",
}


def beyond(text):
    """Why no plan can say the request, or None."""
    for what, rx in BEYOND.items():
        if re.search(rx, text, re.I):
            return f"the request asks for {what}, which a plan cannot say"
    return None


def query(text, usage, llm_fn=None):
    """(query for Virtuoso, None) from the request's plan, or (None, why the request goes to free generation)."""
    from .prompt import finish
    if (why := beyond(text)) is not None:  # no plan call spent on it
        return None, f"plan: {why}"
    plan, note = write(text, usage, llm_fn)
    if plan is None:
        return None, note
    if (why := cue_mismatch(text, plan)) is not None:
        return None, f"plan: {why}"
    try:
        return finish(compile(plan)), None
    except Unsupported as e:
        return None, f"plan: {e}"
    except Exception as e:  # a compiler bug must never leave a request without a query: free generation takes it
        return None, f"plan: the compiler failed ({type(e).__name__}: {e})"


class Unsupported(Exception):
    """A well-formed plan this version cannot compile: the request goes to free generation."""


def check(plan):
    """What is wrong with a plan, in words the generator can act on ([] when nothing). Codes are checked for form and
    code system only: whether they exist would need the database (unknown_codes, for the training data)."""
    if not isinstance(plan, dict) or plan.get("route") not in ("plan", "free"):
        return ['the plan needs "route": "plan" or "free"']
    if plan["route"] == "free":
        return []
    bad, recs = [], plan.get("records") or {}
    unsafe = plan.get("unsafe") or {}
    if unsafe.get("other"):
        return []  # free generation takes it
    for name, r in recs.items():
        if not re.match(NAME, name):
            bad.append(f"record name {name!r}: lower case letters, digits and _ only")
        spec = RECORDS.get(r.get("type"))
        if spec is None:
            bad.append(f"{name}: unknown record type {r.get('type')!r}; one of {', '.join(CURATED)}")
            continue
        for c in r.get("code") or []:
            system = c.split(":", 1)[0]
            if system not in SC.PREFIXES:
                bad.append(f"{name}: code {c!r} has an unknown prefix")
            elif spec["systems"] and system not in spec["systems"]:
                bad.append(f"{name}: a {r['type']} code comes from {' or '.join(spec['systems'])}, not {system}")
        if r.get("code") and "code" not in spec:
            bad.append(f"{name}: a {r['type']} has no code in the data; drop \"code\"")
        for w in r.get("where") or []:
            bad += _test_problems(recs, w, own=name)
    bad += _cond_problems(recs, plan.get("cohort"))
    bad += _same_stay_problems(plan) + _gap_problems(plan) + _compare_problems(plan) + _value_problems(plan)
    out = plan.get("output") or []
    bad += _output_problems(recs, out) + _group_problems(recs, plan.get("group") or [])
    if _grouped(plan) and len(units(plan)) != 1 and not _gap_shape(plan):  # a gap plan relates records (_gap_problems)
        bad.append('grouping, averages and shares work on one record type (others only in "patients_if": {"has": ...}): '
                   'use route "free" for more')
    for a in unsafe.get("rows") or []:
        bad += _attr_problems(recs, a)
    if not out and not unsafe.get("whole") and unsafe.get("form") not in UNSAFE_FORMS:
        bad.append('the plan needs "output", e.g. [{"name": "patients", "agg": "patients"}]')
    return bad


def _grouped(plan):
    """Whether a plan is analytic (v2): grouped, or with an average, sum, share or patients_if."""
    return bool(plan.get("group")) or any(m.get("agg") in V2_AGGS for m in plan.get("output") or [])


def _output_problems(recs, out):
    """What is wrong with the outputs' "of" and "if" (gap outputs: _gap_problems)."""
    bad = []
    for m in out:
        agg, of = m.get("agg"), str(m.get("of", ""))
        if agg in ("first", "last"):
            bad += _attr_problems(recs, of)
        elif agg == "records" and of not in recs:
            bad.append(f"output {m.get('name')}: \"of\" names the record to count")
        elif agg in ("avg", "min", "max", "sum"):
            bad += _attr_problems(recs, of)
            if of.split(".")[-1] not in NUMERIC and \
                    attr_kind((recs.get(of.split(".")[0]) or {}).get("type"), of.split(".")[-1]) != "number":
                bad.append(f"output {m.get('name')}: {agg} of a number ({', '.join(NUMERIC)})")
        elif agg in ("share", "patients_if"):
            cond = m.get("if") or {}
            if "stay_has" in cond:
                pass  # _same_stay_problems
            elif "has" in cond and agg == "patients_if":
                bad += [] if cond["has"] in recs else [f"output {m.get('name')}: no record named {cond['has']!r}"]
            else:
                bad += _test_problems(recs, cond) if cond else [f"output {m.get('name')}: {agg} needs \"if\""]
    return bad


def _group_problems(recs, dims):
    """What is wrong with the code_class and year group keys (stay_has: _same_stay_problems, after: _gap_problems)."""
    bad = []
    for d in dims:
        if "code_class" in d:
            rec, _, alias = str(d["code_class"]).partition(".")
            type_ = (recs.get(rec) or {}).get("type")
            spec = RECORDS.get(type_, {})
            fixed = alias in CODE_ALIASES[1:] and has_alias(spec, alias)  # rank, route; never the unit
            fixed = fixed or alias == "insurance" and alias in extra(type_)  # grouped by the coverage type
            if rec not in recs or (alias and not fixed and alias not in coded(type_)) or (not alias and "code" not in spec):
                bad.append(f"group: {d['code_class']!r} is not a record with a code, nor one of its coded details "
                           f"({', '.join([a for a in CODE_ALIASES[1:] if has_alias(spec, a)] + list(coded(type_))) or 'none'})")
            if d.get("ns") and d["ns"] not in SC.PREFIXES:
                bad.append(f"group: unknown code system {d['ns']!r}")
        elif "year" in d:
            bad += _attr_problems(recs, d["year"])
    return bad


# ---- v3 shapes: one owner each (same stay, gap and what-next, compare, coded values) -------------------------------

# Record types that link to a hospital stay (sphn:hasAdministrativeCase in the 2026.1 schema): same_stay, stay_has.
# A generic type: when its stay link has data (its spec's case).
STAY_TYPES = tuple(t for t in RECORDS if (re.search(r"^    sphn:hasAdministrativeCase ", SC.CFG["blocks"].get(f"sphn:{t}", ""), re.M)
                                          if t in CURATED else RECORDS[t].get("case")))


def _from_stay(name, r, case):
    """A record's lines when its stay (case) is already bound: the link first, then the record, its code class
    checked on the bound code node (an unbound second code or type triple with inference: 50 s on 5k patients;
    0.06 s this way)."""
    lines, spec = _record(name, r), RECORDS[r["type"]]
    if not r.get("code"):  # its type checked on the bound record, without inference (record types are leaf classes)
        return [f"?{name} sphn:hasAdministrativeCase {case} .",
                lines[0].replace(f' OPTION (inference "{S.INFERENCE}")', "")] + lines[1:]
    check, rest = lines[0], lines[1:]
    if spec.get("via"):  # the record's link to the node that holds the code comes first
        via = f"?{name} {spec['via']} ?{name}_test ."
        rest = [via] + [line for line in rest if line != via]
    i = next(i for i, line in enumerate(rest) if line.endswith(f"?{name}_code ."))
    return [f"?{name} sphn:hasAdministrativeCase {case} ."] + rest[:i + 1] + [check] + rest[i + 1:]


def _parts(c):
    """The conditions directly inside an and / or / not."""
    return c.get("and") or c.get("or") or ([c["not"]] if "not" in c else [])


def _stay_pairs(c, out=None):
    """The cohort's same_stay pairs [(a, b)], each once, in order."""
    out = [] if out is None else out
    if isinstance(c, dict):
        if "same_stay" in c and tuple(c["same_stay"]) not in out:
            out.append(tuple(c["same_stay"]))
        for x in _parts(c):
            _stay_pairs(x, out)
    return out


def _stays(plan):
    """The records an analytic plan tests the rows' stays for (group or "if" stay_has), each once, in order."""
    names = [d["stay_has"] for d in plan.get("group") or [] if "stay_has" in d]
    names += [m["if"]["stay_has"] for m in plan.get("output") or [] if "stay_has" in (m.get("if") or {})]
    return list(dict.fromkeys(names))


def _same_stay_problems(plan):
    """What is wrong with the plan's same-stay parts (cohort same_stay, group/if stay_has), [] when nothing."""
    recs, out, bad = plan.get("records") or {}, plan.get("output") or [], []
    unsafe = plan.get("unsafe") or {}

    def linked(where, n):
        if n not in recs:
            return [f"{where}: no record named {n!r}"]
        t = recs[n].get("type")
        return [] if t in STAY_TYPES else [f"{where}: a {t} has no hospital stay; one of "
                                           f"{', '.join(s for s in STAY_TYPES if s in CURATED)}"]
    pairs = _stay_pairs(plan.get("cohort"))
    for a, b in pairs:
        bad += linked("same_stay", a) + linked("same_stay", b) + (["same_stay: names two different records"] if a == b else [])
    if pairs and (any(m.get("agg") != "patients" for m in out) or unsafe.get("rows") or unsafe.get("per_patient")
                  or plan.get("group") or unsafe.get("form") in ("CONSTRUCT", "DELETE", "INSERT")):
        bad.append('same_stay in the cohort counts patients only ("agg": "patients"); use route "free" for more')
    stays = _stays(plan)
    if not stays:
        return bad
    unit = units(plan)
    if not _grouped(plan) or len(unit) != 1:
        bad.append('stay_has splits the rows of one record type: "group": [{"stay_has": "b"}] or a share / patients_if '
                   'with "if": {"stay_has": "b"}, b another record')
    else:
        bad += linked("stay_has", unit[0])
    for n in stays:
        bad += linked("stay_has", n) + ([f"stay_has: {n} is the record counted row by row; name another record"]
                                        if n in unit else [])
    for m in out:
        if "stay_has" in (m.get("if") or {}) and m.get("agg") not in ("share", "patients_if"):
            bad.append(f'output {m.get("name")}: "if": {{"stay_has": ...}} goes with share or patients_if')
        if m.get("agg") == "patients_if" and "has" in (m.get("if") or {}):
            bad.append('stay_has next to "patients_if": {"has": ...}: use route "free"')
    return bad


GAP_NEXT = 3  # records an after/next key may list (2**3 categories)
GAP_UNITS = {"days": "day", "hours": "hour"}


def _gap_shape(plan):
    """Whether a plan measures time to the next records: a gap output or an after/next group key (organizers' example 1)."""
    return any(m.get("agg") == "gap" for m in plan.get("output") or []) or any("after" in d for d in plan.get("group") or [])


def _dates(type_):
    """The date attributes of a record type: date, start, end when it has them, then its schema dates (MEASURES)."""
    spec = RECORDS.get(type_, {})
    return [a for a in ("date", "start", "end") if spec.get(a)] + [a for a, (_, k) in extra(type_).items() if k == "date"]


def _gap_dated(plan):
    """{record: the date attribute a gap plan measures it by}: its gap "from" / "to" attribute, else (absent) its date."""
    out = {}
    for m in plan.get("output") or []:
        if m.get("agg") == "gap":
            for a in [m.get("from"), *(m.get("to") or [])]:
                out.setdefault(*str(a).partition(".")[::2])
    return out


def _plain_cohort(c):
    """Whether a cohort uses only has / and / or / not / before (what the per-patient flags of a gap plan can say)."""
    if c is None or "has" in c or "before" in c:
        return True
    if "not" in c:
        return _plain_cohort(c["not"])
    parts = c.get("and") or c.get("or")
    return bool(parts) and all(map(_plain_cohort, parts))


def _gap_problems(plan):
    """What is wrong with the plan's gap outputs and after/next group keys, [] when nothing."""
    recs, out, dims = plan.get("records") or {}, plan.get("output") or [], plan.get("group") or []
    gaps, keys = [m for m in out if m.get("agg") == "gap"], [d for d in dims if "after" in d]
    if not gaps and not keys:
        return []
    bad, seen, dated = [], {}, _gap_dated(plan)

    def date_problems(rec, alias, where):
        if rec not in recs:
            return [f"{where}: no record named {rec!r}"]
        type_ = recs[rec].get("type")
        if alias not in _dates(type_):
            return [f"{where}: {rec}.{alias} is not a date of a {type_} ({', '.join(_dates(type_)) or 'it has none'})"]
        if seen.setdefault(rec, alias) != alias:
            return [f"{where}: a record is measured by one date per plan ({rec}.{seen[rec]})"]
        return []

    for m in gaps:
        where, to = f"output {m.get('name')}", m.get("to") or []
        if m.get("stat") not in GAP_STATS or m.get("in") not in GAP_UNITS or not m.get("from") or not to:
            bad.append(f'{where}: a gap has "from" (a date), "to" (the next records\' dates), "stat" (avg, min or max) '
                       'and "in" (days or hours)')
        bad += [p for a in [m.get("from"), *to] if a for p in date_problems(*str(a).partition(".")[::2], where)]
        if str(m.get("from")).partition(".")[0] in {str(a).partition(".")[0] for a in to}:
            bad.append(f"{where}: a gap goes from one record to other records")
        if m.get("of") or m.get("if"):
            bad.append(f'{where}: a gap has no "of" nor "if"')
    starts = {str(m.get("from")).partition(".")[0] for m in gaps}
    if len(starts) > 1:
        bad.append("gaps start at one record per plan")
    for d in keys:
        nxt = d.get("next") or []
        for r in [d.get("after"), *nxt]:
            bad += date_problems(r, dated.get(r, "date"), "group")
        if d.get("after") in nxt or len(set(nxt)) != len(nxt) or len(nxt) > GAP_NEXT:
            bad.append(f'group: "next" lists 1 to {GAP_NEXT} other records, each once')
        if starts - {d.get("after")}:
            bad.append(f'group: a gap starts at the record named in "after" ({d.get("after")})')
    if dims and (len(keys) != 1 or len(dims) != 1):
        bad.append('group: an after/next key is the only group key of a gap plan (route "free" for more)')
    if any(m.get("agg") not in ("patients", "gap") for m in out):
        bad.append('a gap or after/next plan outputs "patients" and gaps only (route "free" for more)')
    names = {m.get("name") for m in out}
    bad += [f"order: no output named {o.get('by')!r}" for o in plan.get("order") or [] if o.get("by") not in names]
    return bad


def _gap_query(plan):
    """Time from a record to the next ones, and which of them follow it (organizers' example 1), per patient as the SPHN
    step-D pattern does: one sub-query per record grouped by ?g, combined by UNION and an outer GROUP BY ?g. A next
    record's branch first selects the anchor's first date per patient, then that patient's records strictly after it
    (the organizers' ">"). The category and the gap are expressions in place, no BIND (see _record); the trusted
    query's OPTIONAL joins took over 10 minutes on 100k patients."""
    recs, gaps, unsafe = plan["records"], [m for m in plan["output"] if m["agg"] == "gap"], plan.get("unsafe") or {}
    cohort, key = plan.get("cohort"), next((d for d in plan.get("group") or [] if "after" in d), None)
    if set(unsafe) - {"graph", "service"}:
        raise Unsupported("an unsafe part with a gap: free generation")
    if not _plain_cohort(cohort):
        raise Unsupported("a gap with this cohort: free generation")
    anchor = key["after"] if key else gaps[0]["from"].split(".")[0]
    dated = _gap_dated(plan)
    date = lambda r: f"?{r}_{dated.get(r, 'date')}"
    nexts = list(dict.fromkeys((key["next"] if key else []) + [a.split(".")[0] for m in gaps for a in m["to"]]))

    def pattern(r, alias):
        lines = _record(r, recs[r], (alias,) if alias else (), inline=True)
        if alias and not any(f" ?{r}_{alias} ." in f" {line}" for line in lines):  # an attribute _record does not bind
            lines.append(f"?{r} {RECORDS[recs[r]['type']].get(alias) or extra(recs[r]['type'])[alias][0]} ?{r}_{alias} .")
        return _graph(lines)

    def per_patient(cols, body):
        return _sub(f"SELECT ?g {' '.join(cols)}", body, "GROUP BY ?g")

    first = lambda v: per_patient([f"(MIN({date(anchor)}) AS {v})"], pattern(anchor, dated.get(anchor, "date")))
    branches = [first(f"?{anchor}_first_")]
    cols = [f"(MIN(?{anchor}_first_) AS ?{anchor}_first)"]
    for r in nexts:  # the anchor's first date, once per patient, then the record's dates after it (0.x s, not minutes)
        to = [m["name"] for m in gaps if r in {a.split(".")[0] for a in m["to"]}]
        branches.append(per_patient([f"(MIN({date(r)}) AS ?{r}_next_)", *(f"(MIN({date(r)}) AS ?{n}_to_)" for n in to)],
                                    [*first(f"?{anchor}_from"), *pattern(r, dated.get(r, "date")),
                                     f"FILTER ({date(r)} > ?{anchor}_from)"]))
        cols.append(f"(MIN(?{r}_next_) AS ?{r}_next)")
    cols += [f"(MIN(?{m['name']}_to_) AS ?{m['name']}_to)" for m in gaps]
    for r, rs in _used(cohort).items():  # the cohort's flags and dates, as _flags names them for _expr
        dates = [("first", "MIN", "min"), ("then", "MAX", "max")]
        branches.append(per_patient([f"(COUNT(*) AS ?{r}_n_)"] + [f"({f}(?{r}_date) AS ?{r}_date_{s}_)" for k, f, s in dates if k in rs],
                                    pattern(r, "date" if rs & {"first", "then"} else None)))
        cols += [f"(MAX(?{r}_n_) AS ?{r}_alias)"] + [f"({f}(?{r}_date_{s}_) AS ?{r}_date_{s})" for k, f, s in dates if k in rs]
    union = [line for i, b in enumerate(branches) for line in (["UNION"] if i else []) + b]
    where = per_patient(cols, union) + ([f"FILTER (BOUND(?{anchor}_first) && {_expr(cohort)})"] if cohort else [])
    head, tail, label = [], "", None
    if key:  # the names of the next records that follow, in the plan's order ("surg", "radio", "surg+radio"), or "none"
        def label(i, chosen):
            if i == len(key["next"]):
                return '"' + ("+".join(chosen) or "none") + '"'
            r = key["next"][i]
            return f"IF(BOUND(?{r}_next), {label(i + 1, chosen + [r])}, {label(i + 1, chosen)})"
        head.append("?next")
        tail = "\nGROUP BY ?next"
    if key or cohort:
        # one row per patient (with its category), grouped by that variable: Virtuoso grouped GROUP BY (IF(BOUND ...) AS
        # ?next) row by row and lost the patients without a next record (5k patients: 123 rows, not 4); next to the
        # cohort FILTER it ran datediff on the patients the FILTER drops (DB_NULL error)
        kept = " ".join([f"?{anchor}_first", *(f"?{m['name']}_to" for m in gaps)])
        where = _sub(f"SELECT ?g {f'({label(0, [])} AS ?next) ' if key else ''}{kept}", where)
    for m in plan["output"]:
        n = m["name"]
        if m["agg"] == "patients":
            head.append(f"(COUNT(DISTINCT ?g) AS ?{n})")
            continue
        # Virtuoso may run datediff before the IF picks a branch (DB_NULL error): its arguments are never unbound, as
        # in the organizers' query (COALESCE), each falling back on the other date first (a constant next to a data
        # date: "mixed timezoned and timezoneless" error); a patient without a next record counts nowhere (?<name>_none)
        a, b, zero = f"?{anchor}_first", f"?{n}_to", '"1900-01-01T00:00:00Z"^^xsd:dateTime'
        diff = (f'IF(BOUND({b}) && BOUND({a}), bif:datediff("{GAP_UNITS[m["in"]]}", COALESCE({a}, {b}, {zero}), '
                f'COALESCE({b}, {a}, {zero})), ?{n}_none)')
        e = {"avg": f"AVG(1.0 * {diff})", "min": f"MIN({diff})", "max": f"MAX({diff})"}[m["stat"]]
        head.append(f"(ROUND({e}) AS ?{n})" if m.get("round") else f"({e} AS ?{n})")
    return _wrap(_query("SELECT " + " ".join(head), where, tail + _order_limit(plan)), unsafe)


# compare: {"left": "a.attr", "op": "=" | "!=", "right": "b.attr"}: the patients with an a whose attribute equals that
# of one of their b's ("="), or that of none of their b's ("!="; compile() adds that they have a b: "born in a country
# they aren't a citizen of" counts only patients with a nationality, as the organizers' example 4 does). Sides of one kind: code
# classes (a record's code, country, rank, route, coded details), value-set individuals, numbers, dates (by the day).
# A country is a class of the 2026.1 schema's value set for sphn:Country's code (descendants of these two SNOMED CT
# concepts; the trusted query of the organizers' example 4 keeps only those birth countries).
COUNTRY_ROOTS = ("snomed:223369002", "snomed:223860005")
COMPARE_CODES = ("code", "country", "rank", "route")
VERSIONED = {"icd-10-gm", "chop"}  # their classes are per year version (.../2024/E11.9): compared without the year


def _compare_side(recs, attr):
    """(record, alias, kind) of one side of a compare; kind "code", "individual", "number" or "date", None when the
    record has no such attribute to compare."""
    rec, _, alias = str(attr).partition(".")
    type_ = (recs.get(rec) or {}).get("type") if isinstance(recs.get(rec), dict) else None
    spec, kind = RECORDS.get(type_) or {}, None
    if alias in COMPARE_CODES:
        kind = "code" if spec.get(alias) else None
    elif alias in coded(type_):
        kind = {"code": "code", "value": "individual"}.get(coded(type_)[alias][1])
    elif alias in NUMERIC or alias in ("date", "start", "end"):
        kind = ("number" if alias in NUMERIC else "date") if has_alias(spec, alias) else None
    return rec, alias, kind


def _compare_systems(recs, rec, alias):
    """The code systems a code side's classes come from (None when unknown)."""
    if alias == "code":
        return set(RECORDS[recs[rec]["type"]]["systems"]) or None
    return {"snomed"} if alias in ("country", "rank", "route") else None


def _compares(c, out=None):
    """The compare conditions of a cohort, in order."""
    out = [] if out is None else out
    if isinstance(c, dict):
        if isinstance(c.get("compare"), dict):
            out.append(c["compare"])
        for x in _parts(c):
            _compares(x, out)
    return out


def _compare_problems(plan):
    """What is wrong with the plan's compare conditions, [] when nothing."""
    recs, bad = plan.get("records") or {}, []
    found = _compares(plan.get("cohort"))
    if found and (plan.get("group") or any(m.get("agg") != "patients" for m in plan.get("output") or [])):
        bad.append('compare: a plan with a compare counts patients ({"agg": "patients"}); use route "free" for more')
    for cmp in found:
        sides = [_compare_side(recs, cmp.get(k)) for k in ("left", "right")]
        for k, (rec, alias, kind) in zip(("left", "right"), sides):
            if rec not in recs:
                bad.append(f"compare: {k} {cmp.get(k)!r} names no record")
            elif kind is None:
                t = recs[rec].get("type")
                can = [a for a in (*COMPARE_CODES, *coded(t), *NUMERIC, "date", "start", "end")
                       if _compare_side(recs, f"{rec}.{a}")[2]]
                bad.append(f"compare: a {t} has no {alias} to compare ({', '.join(can) or 'nothing'})")
        if cmp.get("op") not in ("=", "!="):
            bad.append('compare: op is "=" or "!="')
        if any(rec not in recs or kind is None for rec, _, kind in sides):
            continue
        (a, a_alias, a_kind), (b, b_alias, b_kind) = sides
        if a == b:
            bad.append(f"compare: two different records, not {a} with itself")
        elif a_kind != b_kind:
            bad.append(f"compare: {cmp['left']} ({a_kind}) and {cmp['right']} ({b_kind}): compare the same kind")
        elif a_kind == "code" and None not in (sa := _compare_systems(recs, a, a_alias), sb := _compare_systems(recs, b, b_alias)) \
                and not sa & sb:
            bad.append(f"compare: {cmp['left']} ({', '.join(sorted(sa))}) and {cmp['right']} ({', '.join(sorted(sb))}) "
                       "share no code system")
    return bad


def _compare_bind(recs, attr, kind, left=True):
    """(lines, value, generic, after): one side's record pattern binding its attribute (the code triple first), the
    value compared as an expression, a FILTER that drops the generic SPHN classes of a code side (or None), and what
    follows the patient's graph: the left country's check against the Country value set (the right one joins on the
    left's class). The value-set check as an inference UNION inside the sub-query timed out (> 10 s); as subClassOf*
    in the terminology graph it takes 0.06 s."""
    rec, alias = attr.split(".")
    r = recs[rec]
    spec = RECORDS[r["type"]]
    var, generic, after = f"?{rec}_{alias}", None, []
    if alias == "country":
        lines = [*_record(rec, r, inline=True), f"?{rec} {spec['country']} {var}_code .", f"{var}_code rdf:type {var} ."]
        if not left:
            lines = lines[-1:] + lines[-2:-1] + lines[:-2]  # the join on the left's class first
        else:
            after = [f"VALUES {var}_root {{ {' '.join(f'<{_iri(c)}>' for c in COUNTRY_ROOTS)} }}",
                     f"GRAPH <{S.INFERENCE}> {{ {var} rdfs:subClassOf* {var}_root . }}"]
    elif alias == "code":
        var = f"?{rec}_code_class"
        lines = _record(rec, r, code_node=True, inline=True)
        lines = [f"?{rec}_code rdf:type {var} .", *lines] if not left else lines + [f"?{rec}_code rdf:type {var} ."]
    else:
        lines = _record(rec, r, (alias,), inline=True)
    typed = f'?{rec} rdf:type sphn:{r["type"]} OPTION (inference "{S.INFERENCE}") .'
    if typed in lines and (not left or alias == "country"):  # with NOT EXISTS, an inference on it timed out (> 30 s)
        lines = [line for line in lines if line != typed] + [f"?{rec} rdf:type sphn:{r['type']} ."]
    value = var
    if kind == "code":
        generic = f'FILTER (!STRSTARTS(STR({var}), "{SC.NS}"))'
        if alias == "code" and set(spec["systems"]) & VERSIONED:
            value = f'REPLACE(STR({var}), "/(19|20)[0-9][0-9]/", "/")'
    elif kind == "number":
        value = _computed(rec, alias) or var
    elif kind == "date":
        value = f"SUBSTR(STR({var}), 1, 10)"
    return lines, value, generic, after


def _compare_name(cmp):
    """The flag variable stem of one compare condition."""
    return "cmp_" + "_".join([cmp["left"].replace(".", "_"), "eq" if cmp["op"] == "=" else "ne", cmp["right"].replace(".", "_")])


def _compare_branch(recs, cmp):
    """The WHERE lines of the patients for whom one compare condition holds: a's pattern in the patient's graph, then
    b's (within FILTER NOT EXISTS for "!="), joined on the compared value; two records of one type are two records."""
    kind = _compare_side(recs, cmp["left"])[2]
    a_lines, a_value, a_generic, after = _compare_bind(recs, cmp["left"], kind)
    b_lines, b_value, _, _ = _compare_bind(recs, cmp["right"], kind, left=False)
    a, b = cmp["left"].split(".")[0], cmp["right"].split(".")[0]
    if a_value.startswith("REPLACE") != b_value.startswith("REPLACE"):  # one side versioned: both without the year
        a_value, b_value = (v if v.startswith("REPLACE") else f'REPLACE(STR({v}), "/(19|20)[0-9][0-9]/", "/")'
                            for v in (a_value, b_value))
    tests = [f"?{a} != ?{b}"] if recs[a]["type"] == recs[b]["type"] else []
    if kind in ("code", "individual") and a_value.startswith("?") and b_value.startswith("?"):
        # two IRIs: b's class is a's variable, an index lookup instead of a FILTER over every pair
        b_lines = [re.sub(rf"{re.escape(b_value)}(?!\w)", a_value, line) for line in b_lines]
    else:
        tests.insert(0, f"{a_value} = {b_value}")
        if b_lines[0].startswith(f"?{b}_code rdf:type ?"):  # an unjoined class triple first would scan every type
            b_lines = b_lines[1:] + b_lines[:1]
    match = [f"FILTER ({' && '.join(tests)})"] if tests else []
    if cmp["op"] == "=":  # a generic SPHN class both codes have is no match
        return [*_graph([*a_lines, *([a_generic] if a_generic else [])]), *after, *_graph(b_lines), *match]
    # "!=": no generic-class FILTER (a class b shares only drops a row; next to NOT EXISTS it timed out, > 20 s)
    return [*_graph(a_lines), *after, "FILTER NOT EXISTS {", *_indent([*_graph(b_lines), *match]), "}"]


def _value_problems(plan):
    """Coded-detail values the schema does not allow (with the allowed ones), [] when nothing: every "where" condition
    and output "if" on a coded detail (and on rank, route) of a record type (resources/coded_values.json)."""
    recs, bad = plan.get("records") or {}, []
    tests = [w for r in recs.values() if isinstance(r, dict) for w in r.get("where") or []]
    tests += [m.get("if") for m in plan.get("output") or [] if isinstance(m, dict)]
    for t in tests:
        if not isinstance(t, dict) or "attr" not in t:
            continue
        rec, _, alias = str(t["attr"]).partition(".")
        type_ = (recs.get(rec) or {}).get("type") if isinstance(recs.get(rec), dict) else None
        allowed = CODED_VALUES.get(type_, {}).get(alias)
        if allowed and isinstance(t.get("value"), str) and t["value"] and (why := _value_wrong(type_, alias, t["value"])):
            bad.append(f"{t['attr']}: {why}")
        elif not allowed and isinstance(t.get("value"), str) and t["value"] and (why := _generic_wrong(type_, alias, t["value"])):
            bad.append(f"{t['attr']}: {why}")
    return bad


def _generic_wrong(type_, alias, value):
    """Why a generic detail's value is not one the schema allows (None when it is, or when the schema lists none): a
    value-set individual of its list, or a code (prefix:code) from its code systems."""
    path, kind, *allowed = GENERIC.get(type_, {}).get(alias, (None, None))
    allowed = allowed[0] if allowed else []
    if kind == "value" and allowed:
        local = value[len(VALUE_SET):] if value.startswith(VALUE_SET) else value
        return None if local in allowed else f"{value!r} is not a value of {alias}; one of {_short_list(allowed)}"
    if kind == "code":
        system = value.split(":", 1)[0]
        if not re.match(CODE_FORM, value) or system not in SC.PREFIXES or system in NOT_CODES:
            return f"{value!r} is not a code; {alias} is a code (prefix:code, e.g. {(allowed or ['snomed'])[0]}:...)"
        if allowed and system not in allowed:
            return f"{value!r} is not an allowed {alias} code; a {' or '.join(allowed)} code"
    return None


# What the SPHN 2026.1 schema allows for each coded detail (python -m nl2q.tools.coded_values).
CODED_VALUES = json.loads((HERE / "resources" / "coded_values.json").read_text())
VALUE_SET = "sphn-individual:"
NOT_CODES = ("sphn", "sphn-individual", "rdf", "rdfs", "owl", "bif", "xsd")


def _short_list(items, n=10):
    items = list(items)
    return ", ".join(items[:n]) + (", ..." if len(items) > n else "")


def _value_wrong(type_, alias, value):
    """Why a coded detail's value is not one the schema allows (None when it is), with the allowed ones."""
    d = CODED_VALUES[type_][alias]
    if d["kind"] == "value":
        local = value[len(VALUE_SET):] if value.startswith(VALUE_SET) else value
        if local in d["values"]:
            return None
        elsewhere = [a for a, o in CODED_VALUES[type_].items() if a != alias and local in o.get("values", ())]
        hint = f" ({local} is a value of {type_} {' / '.join(elsewhere)})" if elsewhere else ""
        return f"{value!r} is not a value of {alias}; one of {_short_list(d['values'])}{hint}"
    if d["kind"] == "class":
        if value in d["values"]:
            return None
        return f"{value!r} is not a class of {alias}; one of {_short_list(d['values'])}"
    if alias == "rank" and value == "main":
        return None
    system = value.split(":", 1)[0]
    if d["closed"]:
        if value in d["codes"]:
            return None
        return f"{value!r} is not an allowed {alias} code; one of {_short_list(d['codes'])}" + \
            (' ("main" for the principal rank)' if alias == "rank" else "")
    if re.match(CODE_FORM, value) and system in SC.PREFIXES and system not in NOT_CODES and \
            (not d["systems"] or system in d["systems"]):
        return None
    if not d["systems"]:
        return f"{value!r} is not a code; {alias} is a code (prefix:code, e.g. snomed:...)"
    under = f" under {_short_list(d['codes'], 3)}" if d["codes"] else ""
    examples = f" (e.g. {_short_list(d['examples'], 4)})" if d.get("examples") else ""
    return f"{value!r} is not an allowed {alias} code; a {' or '.join(d['systems'])} code{under}{examples}"


def units(plan):
    """The records a v2 plan describes row by row: all but those only named in "patients_if": {"has": ...}."""
    out = plan.get("output") or []
    flags = {(m.get("if") or {}).get("has") for m in out if m.get("agg") == "patients_if"} - {None}
    flags |= set(_stays(plan))  # records only tested in the rows' stays (stay_has)
    used = {str(a).split(".")[0] for m in out for a in (m.get("of"), (m.get("if") or {}).get("attr")) if a}
    used |= {str(d.get("year") or d.get("code_class")).split(".")[0] for d in plan.get("group") or []}
    return [r for r in (plan.get("records") or {}) if r not in flags or r in used]


def _attr_problems(recs, attr, own=None):
    rec, _, alias = str(attr).partition(".")
    if rec not in recs:
        return [f"{attr!r}: no record named {rec!r}"]
    if own and rec != own:
        return [f"{attr!r}: a record's conditions are on its own attributes ({own}.*)"]
    type_ = recs[rec].get("type")
    if not ((alias in ALIASES and has_alias(RECORDS.get(type_, {}), alias)) or alias in coded(type_) or alias in extra(type_)):
        return [f"{attr!r}: a {type_} has no {alias}"]
    return []


def _test_problems(recs, t, own=None):
    """A condition on one attribute: {"attr": "r.alias", "op": ..., "value": ...}."""
    name = own or str(t.get("attr", "")).partition(".")[0]
    bad = _attr_problems(recs, t.get("attr", ""), own=own)
    alias, op, value = str(t.get("attr", ".")).split(".")[-1], t.get("op"), t.get("value")
    if op not in OPS:
        bad.append(f"{name}: unknown operator {op!r}")
    type_ = (recs.get(name) or {}).get("type")
    kind = attr_kind(type_, alias) if alias in extra(type_) else None
    if (alias in NUMERIC or kind == "number") and (isinstance(value, bool) or not isinstance(value, (int, float))):
        bad.append(f"{name}: {t.get('attr')} compares with a number")
    if (alias == "unit" or kind == "unit") and (op != "=" or not str(value).startswith("ucum:")):
        bad.append(f"{name}: a unit is matched with \"=\" and a ucum: code")
    if (kind == "date" or alias in ("date", "start", "end")) and not re.match(r"^\d{4}-\d{2}-\d{2}", str(value)):
        bad.append(f"{name}: a date is written YYYY-MM-DD")
    if (alias in ("rank", "route") or alias in coded(type_) or kind == "value") and \
            (op not in ("=", "!=") or not isinstance(value, str) or not value):
        bad.append(f'{name}: {alias} is compared with "=" and a code or value ("main" for the principal rank)')
    if alias == "in_stay" and value not in (True, False):
        bad.append(f"{name}: in_stay is true or false")
    return bad


def _cond_problems(recs, c):
    if c is None:
        return []
    if "has" in c:
        return [] if c["has"] in recs else [f"cohort: no record named {c['has']!r}"]
    if "same_stay" in c:
        return []  # _same_stay_problems
    if "compare" in c:
        return []  # _compare_problems
    if "and" in c or "or" in c:
        return [p for x in c.get("and") or c.get("or") for p in _cond_problems(recs, x)]
    if "not" in c:
        return _cond_problems(recs, c["not"])
    if "before" in c:
        b = c["before"]
        bad = _attr_problems(recs, b.get("first", "")) + _attr_problems(recs, b.get("then", ""))
        if not all(str(b.get(k, "")).endswith(".date") for k in ("first", "then")):
            bad.append("before: compares two dates, e.g. {\"first\": \"dx.date\", \"then\": \"lab.date\"}")
        if b.get("gap") and not re.match(DURATION, b["gap"]):
            bad.append('before: gap is a duration such as "P7D"')
        return bad
    return [f"cohort: unknown condition {sorted(c)}"]


def codes(plan):
    """Every code class the plan names, as IRIs."""
    out = {_iri(c) for r in (plan.get("records") or {}).values() for c in r.get("code") or []}
    return out | {_iri(w["value"]) for r in (plan.get("records") or {}).values() for w in r.get("where") or []
                  if re.search(r"\.(?:\w+_)?unit$", w.get("attr", ""))}


def unknown_codes(plan, run=None):
    """The plan's code classes the terminologies loaded in Virtuoso do not have (empty when the database is out of
    reach). Offline only (nl2q/training keeps the plans whose codes exist); never called while answering. ICD-10-GM and
    CHOP are not looked up: their leaf codes exist only per year version, which OPTION (inference ...) reaches from the
    unversioned code."""
    versioned = tuple(SC.PREFIXES[p] for p in ("icd-10-gm", "chop"))
    iris = sorted(i for i in codes(plan) if not i.startswith(versioned))
    if not iris:
        return []
    try:
        from ..store import run as store_run
        _, rows = (run or store_run)(f"SELECT DISTINCT ?c WHERE {{ VALUES ?c {{ {' '.join(f'<{i}>' for i in iris)} }} "
                                     f"GRAPH <{S.INFERENCE}> {{ ?c ?p ?o }} }}", timeout=10)
    except Exception:
        return []
    return [i for i in iris if i not in {r[0] for r in rows}]


# ---- compile -------------------------------------------------------------------------------------------------------

def _iri(code):
    code = str(code)
    if code.startswith("<"):
        return code[1:-1]
    if code.startswith(("http://", "https://")):
        return code
    prefix, _, local = code.partition(":")
    return SC.PREFIXES.get(prefix, prefix + ":") + local


def _infer(code, spec):
    return spec.get("inference", True) and code.split(":", 1)[0] in HIERARCHY


def _literal(v):
    if isinstance(v, (int, float)):
        return repr(v)
    v = str(v)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        v += "T00:00:00"
    return f'"{v}"^^xsd:dateTime' if re.match(r"^\d{4}-\d{2}-\d{2}T", v) else '"' + v.replace('"', '\\"') + '"'


def _computed(name, alias):
    """A computed attribute's expression (hours, days, in_stay), or None for a stored one."""
    if alias in ("hours", "days"):
        return f'bif:datediff("{alias[:-1]}", ?{name}_start, ?{name}_end)'
    if alias == "in_stay":
        return f"IF(?{name}_start >= ?{name}_admission && ?{name}_start <= ?{name}_discharge, 1, 0)"
    return None


def _test_expr(t, guard=False, recs=None, inline=False):
    """A condition on one attribute as a SPARQL expression; guard: false (not an error) when the attribute is unbound.
    recs: the plan's records, to tell a coded detail's code (class) from its value-set individual. inline: a computed
    attribute as its expression, not its BIND variable (see _record)."""
    rec, alias = t["attr"].split(".")
    var, value = f"?{rec}_{alias}", t["value"]
    expr = _computed(rec, alias) if inline else None
    kind = attr_kind(((recs or {}).get(rec) or {}).get("type"), alias)
    if alias in CODE_ALIASES or kind in ("code", "class", "unit"):
        value = f"<{_iri(MAIN if value == 'main' else value)}>"
    elif kind == "value":  # a value-set individual: sphn-individual:Fatal, or just Fatal
        value = f"<{_iri(value if ':' in str(value) else 'sphn-individual:' + str(value))}>"
    elif alias == "in_stay":
        value = "1" if value is True else "0"
    else:
        value = _literal(value)
    if expr:  # an error in the expression counts as false, as the unbound BIND variable did
        return f"COALESCE({expr} {t['op']} {value}, false)" if guard else f"{expr} {t['op']} {value}"
    return f"(BOUND({var}) && {var} {t['op']} {value})" if guard else f"{var} {t['op']} {value}"


def _chain(node, path, end, name):
    """A property path as one triple per step, written from its end (the code node, already bound): Virtuoso walks
    "p1/p2/p3" from its start, which at 100k patients made a coded-drug lookup take 28 s instead of 0.05 s."""
    steps = path.split("/")
    if len(steps) == 1:
        return f"{node} {path} {end} ."
    nodes = [node, *[f"?{name}_step{i}" for i in range(len(steps) - 1)], end]
    return " ".join(f"{nodes[i]} {steps[i]} {nodes[i + 1]} ." for i in reversed(range(len(steps))))


def _record(name, r, attrs=(), code_node=False, inline=False):
    """Triple patterns (one per line) for one record and the attributes it must bind: the code triple first, with
    OPTION (inference ...) on it only, then the link to the record, its type, then each condition with its FILTER
    right after the variable it binds. code_node: bind ?<name>_code even without a code condition (to group by it).
    inline: no BIND for a computed attribute (hours, days, in_stay) in attrs; the caller writes its expression
    (_computed) where it is used, as conditions always do. Why: under DEFINE sql:select-option "order", a BIND next to
    an OPTION (inference ...) triple makes Virtuoso time out (organizers' example 7: > 30 s, 0.1 s without, same rows)."""
    spec = RECORDS[r["type"]]
    node = f"?{name}_test" if spec.get("via") else f"?{name}"
    lines, codes_ = [], r.get("code") or []
    if codes_:
        alts = [f"?{name}_code rdf:type <{_iri(c)}>" + (f' OPTION (inference "{S.INFERENCE}")' if _infer(c, spec) else "") + " ."
                for c in codes_]
        lines.append(alts[0] if len(alts) == 1 else " UNION ".join("{ " + a + " }" for a in alts))
        lines.append(_chain(node, spec["code"], f"?{name}_code", name))
        if spec.get("via"):
            lines.append(f"?{name} {spec['via']} {node} .")
        if spec.get("typed", True):  # a generic type with sub-classes (Sample: TumorSpecimen, ...) counts them too
            lines.append(f"?{name} rdf:type sphn:{r['type']}" +
                         (f' OPTION (inference "{S.INFERENCE}")' if spec.get("subclasses") else "") + " .")
    else:
        lines.append(f'?{name} rdf:type sphn:{r["type"]} OPTION (inference "{S.INFERENCE}") .')
        if spec.get("via"):
            lines.append(f"?{name} {spec['via']} {node} .")
        if code_node:
            lines.append(f"{node} {spec['code']} ?{name}_code .")
    if r.get("identifier"):
        lines.append(f'?{name} sphn:hasIdentifier {_literal(r["identifier"])} .')
    bound = set()

    def bind(alias):
        if alias in bound:
            return
        bound.add(alias)
        var = f"?{name}_{alias}"
        if alias in ("value", "unit"):
            if "quantity" not in bound:
                bound.add("quantity")
                lines.append(f"{node if spec['value'] == RESULT else '?' + name} {spec['value']} ?{name}_quantity .")
            lines.append(f"?{name}_quantity sphn:hasValue {var} ." if alias == "value" else
                         f"?{name}_quantity sphn:hasUnit/sphn:hasCode {var} .")
        elif alias == "age":
            lines.append(f"?{name} sphn:hasSubjectAge {var}_node . {var}_node sphn:hasQuantity/sphn:hasValue {var} .")
        elif alias in ("date", "start", "end"):
            lines.append(f"?{name} {spec[alias]} {var} .")
        elif alias in ("rank", "route"):
            lines.append(f"?{name} {spec[alias]} {var}_code . {var}_code rdf:type {var} .")
        elif alias in coded(r["type"]):  # a coded detail: its code's (or linked record's) class, or its value-set individual
            prop, kind = coded(r["type"])[alias]
            lines.append(f"?{name} {prop} {var}_code . {var}_code rdf:type {var} ." if kind in ("code", "class") else
                         f"?{name} {prop} {var} .")
        elif alias in extra(r["type"]):  # a schema attribute (MEASURES): a quantity's value or unit, a date, the insurance
            path, kind = extra(r["type"])[alias]
            base = alias[:-len("_unit")] if kind == "unit" else alias
            q = f"?{name}_{base}_quantity"
            if kind in ("number", "unit") and f"{base}:quantity" not in bound:
                bound.add(f"{base}:quantity")
                lines.append(f"?{name} {path} {q} .")
            if kind == "number":
                lines.append(f"{q} sphn:hasValue {var} .")
            elif kind == "unit":
                lines.append(f"{q} sphn:hasUnit/sphn:hasCode {var} .")
            elif kind == "date":
                lines.append(f"?{name} {path} {var} .")
            else:  # the InsuranceStatus of the record's stay (of the stay itself for an AdministrativeCase)
                case = f"?{name}" if r["type"] == "AdministrativeCase" else f"?{name}_case"
                lines.append(("" if case == f"?{name}" else f"?{name} sphn:hasAdministrativeCase {case} . ") +
                             f"{var}_status sphn:hasAdministrativeCase {case} . {var}_status rdf:type sphn:InsuranceStatus . "
                             f"{var}_status {path} {var} .")
        elif alias in ("hours", "days"):
            bind("start"), bind("end")
            lines.extend([] if inline or alias not in attrs else [f"BIND ({_computed(name, alias)} AS {var})"])
        elif alias == "in_stay":
            bind("start")
            lines.append(f"?{name} sphn:hasAdministrativeCase ?{name}_case . ?{name}_case sphn:hasAdmission/sphn:hasDateTime "
                         f"?{name}_admission ; sphn:hasDischarge/sphn:hasDateTime ?{name}_discharge .")
            lines.extend([] if inline or alias not in attrs else [f"BIND ({_computed(name, alias)} AS {var})"])

    for w in r.get("where") or []:
        alias = w["attr"].split(".")[1]
        bind(alias)
        if alias == "unit" or attr_kind(r["type"], alias) == "unit":
            lines.append(f"?{name}_{alias} rdf:type <{_iri(w['value'])}> .")
        else:
            lines.append(f"FILTER ({_test_expr(w, recs={name: r}, inline=inline or alias not in attrs)})")
    for a in attrs:
        bind(a)
    return lines


def _indent(lines, n=1):
    """The lines (an item may hold several) indented by n levels, as a flat list."""
    return ["    " * n + line for x in lines for line in x.split("\n")]


def _graph(lines, extra=()):
    return ["GRAPH ?g {", *_indent(lines), *_indent(extra), "}"]


def _sub(head, body, *after):
    """A sub-query in braces: head (SELECT ...), WHERE { body }, then the lines after it (GROUP BY ...)."""
    return ["{", f"    {head}", "    WHERE {", *_indent(body, 2), "    }", *(f"    {a}" for a in after), "}"]


def _union(branches):
    """The branches (lists of lines), each in braces, joined by UNION."""
    return [line for i, b in enumerate(branches) for line in (["UNION"] if i else []) + ["{", *_indent(b), "}"]]


def _order_limit(plan):
    """The query's ORDER BY and LIMIT lines ("" when the plan has neither)."""
    order = " ".join(f"DESC(?{o['by']})" if o.get("desc") else f"?{o['by']}" for o in plan.get("order") or [])
    return (f"\nORDER BY {order}" if order else "") + (f"\nLIMIT {int(plan['limit'])}" if plan.get("limit") else "")


def _expr(c):
    """The cohort as a FILTER over the per-patient flags (?<record>_alias) and dates."""
    if "has" in c:
        return f"BOUND(?{c['has']}_alias)"
    if "and" in c or "or" in c:
        return "(" + (" && " if "and" in c else " || ").join(_expr(x) for x in c.get("and") or c.get("or")) + ")"
    if "not" in c:
        return f"!{_expr(c['not'])}"
    if "same_stay" in c:  # the pair's branch in _flags
        return f"BOUND(?{'_'.join(c['same_stay'])}_stay_alias)"
    if "compare" in c:  # the condition's branch in _flags
        return f"BOUND(?{_compare_name(c['compare'])}_alias)"
    b = c["before"]
    first, then = b["first"].split(".")[0], b["then"].split(".")[0]
    later = f"?{then}_date_max" + (f' - "{b["gap"]}"^^xsd:duration' if b.get("gap") else "")
    return f"(BOUND(?{first}_alias) && BOUND(?{then}_alias) && ?{first}_date_min < {later})"


def _used(c, out=None):
    """{record: set of roles} the cohort needs: 'flag', 'first' (earliest date), 'then' (latest date)."""
    out = {} if out is None else out
    if c is None:
        return out
    if "has" in c:
        out.setdefault(c["has"], set()).add("flag")
    for x in _parts(c):
        _used(x, out)
    if "before" in c:
        out.setdefault(c["before"]["first"].split(".")[0], set()).update({"flag", "first"})
        out.setdefault(c["before"]["then"].split(".")[0], set()).update({"flag", "then"})
    return out


def _holds_for_nobody(c):
    """True when a patient with none of the records passes the cohort (it has a top-level 'not'): then every patient
    needs a row (WITHOUT.rq's all-patients branch)."""
    if c is None or "has" in c or "before" in c or "same_stay" in c or "compare" in c:
        return False
    if "not" in c:
        return not _holds_for_nobody(c["not"])
    parts = [_holds_for_nobody(x) for x in c.get("and") or c.get("or")]
    return all(parts) if "and" in c else any(parts)


def _flags(plan):
    """WHERE content and patient variable of the flag pattern (AND.rq, OR.rq, WITHOUT.rq, BEFORE.rq): a UNION branch
    per record, same-stay pair and compare flags its patients (?<key>_alias), grouped by patient, then the cohort."""
    recs, cohort = plan["records"], plan.get("cohort")
    branches, outer = [], []

    def flag(key, body, keep=""):
        branches.append([f"SELECT ?pseudo_id (?{key}_filter_pre AS ?{key}_filter){keep}", "WHERE {",
                         *_indent(_sub(f"SELECT DISTINCT (?g AS ?pseudo_id){keep}", body)),
                         f'    BIND ("true"^^xsd:boolean AS ?{key}_filter_pre)', "}"])
        outer.append(f"(MAX(?{key}_filter) AS ?{key}_alias)")

    for name, rs in _used(cohort).items():
        dated = rs & {"first", "then"}
        flag(name, _graph(_record(name, recs[name], ("date",) if dated else ())), f" ?{name}_date" if dated else "")
        if "first" in rs:
            outer.append(f"(MIN(?{name}_date) AS ?{name}_date_min)")
        if "then" in rs:
            outer.append(f"(MAX(?{name}_date) AS ?{name}_date_max)")
    for a, b in _stay_pairs(cohort):  # both records in one hospital stay (the same sphn:AdministrativeCase)
        key, case = f"{a}_{b}_stay", f"?{a}_{b}_case"
        if recs[b].get("code") and not recs[a].get("code"):  # the coded record first: its code triple starts it
            a, b = b, a
        flag(key, _graph(_record(a, recs[a]) + [f"?{a} sphn:hasAdministrativeCase {case} ."] + _from_stay(b, recs[b], case)))
    for key, cmp in {_compare_name(c): c for c in _compares(cohort)}.items():  # a's attribute equals (none of) b's
        flag(key, _compare_branch(recs, cmp))
    everyone = _holds_for_nobody(cohort)
    if everyone:
        branches.append(["SELECT (?g AS ?pseudo_id) ?everyone_filter", "WHERE {",
                         *_indent(_sub("SELECT DISTINCT ?g", ["GRAPH ?g { ?subject rdf:type sphn:SubjectPseudoIdentifier . }"])),
                         '    BIND ("true"^^xsd:boolean AS ?everyone_filter)', "}"])
        outer.append("(MAX(?everyone_filter) AS ?everyone_alias)")
    expr = ("BOUND(?everyone_alias) && " if everyone else "") + _expr(cohort)
    return [*_sub("SELECT ?pseudo_id " + " ".join(outer), _union(branches), "GROUP BY ?pseudo_id"), f"FILTER ({expr})"], "?pseudo_id"


def _simple(plan):
    """One record (or none): the brief's worked example, a DISTINCT sub-query over the patient graphs."""
    recs = plan.get("records") or {}
    if not recs:  # every patient (All.rq), inside the patient graphs so that unsafe.graph still applies
        return ["GRAPH ?g { ?pseudo_id rdf:type sphn:SubjectPseudoIdentifier . }"], "?pseudo_id"
    name, r = next(iter(recs.items()))
    return _sub("SELECT DISTINCT (?g AS ?pseudo_id)", _graph(_record(name, r))), "?pseudo_id"


def _records_query(plan):
    """(head, WHERE lines, tail) of a query about one record type: counted by record (and patient), with first/last
    dates, raw rows or one row per patient."""
    out, unsafe, cohort = plan.get("output") or [], plan.get("unsafe") or {}, plan.get("cohort")
    if cohort is not None and set(cohort) != {"has"}:
        raise Unsupported("records, first/last dates and rows with conditions across records: next version")
    recs = plan["records"]
    if len(recs) != 1:
        raise Unsupported("counting records, first/last dates and rows: one record type per plan in this version")
    name, r = next(iter(recs.items()))
    attrs = {a.split(".")[1] for a in unsafe.get("rows") or []}
    attrs |= {m["of"].split(".")[1] for m in out if m["agg"] in ("first", "last")}
    lines = _graph(_record(name, r, sorted(attrs)))
    agg = {"patients": "COUNT(DISTINCT ?g)", "records": f"COUNT(DISTINCT ?{name})", "first": f"MIN(?{name}_date)",
           "last": f"MAX(?{name}_date)"}
    items = [f"({agg[m['agg']]} AS ?{m['name']})" for m in out]
    rows = [f"?{name}_{a.split('.')[1]}" for a in unsafe.get("rows") or []]
    if unsafe.get("form") in ("DESCRIBE", "ASK"):
        return ("DESCRIBE ?" + name if unsafe["form"] == "DESCRIBE" else "ASK"), lines, ""
    if rows:
        return "SELECT " + " ".join(rows), lines, ""
    if unsafe.get("per_patient"):
        return "SELECT ?g " + " ".join(items), lines, "\nGROUP BY ?g"
    return "SELECT " + " ".join(items), lines, ""


def _analytic(plan):
    """v2: rows of one record type, grouped (code class, sex, year) and measured (patients, records, avg/min/max/sum,
    share of the rows meeting a condition, patients meeting it), as the organizers' examples 3, 6 and 7 do: one
    pattern inside GRAPH ?g, the record's code triple first; code classes grouped by their code (the year versions of
    ICD-10-GM and CHOP merge), never by the code node; an optional attribute (rank) only where a share needs it."""
    if _gap_shape(plan):  # time to the next records, and which follow (organizers' example 1)
        return _gap_query(plan)
    recs, out, unsafe = plan["records"], plan.get("output") or [], plan.get("unsafe") or {}
    unit = units(plan)
    if len(unit) != 1:
        raise Unsupported("grouping, averages and shares: one record type per plan (others only as patients_if has)")
    if set(unsafe) - {"graph", "service"}:
        raise Unsupported("an unsafe part with grouping or averages: free generation")
    name = unit[0]
    r = recs[name]
    spec, dims = RECORDS[r["type"]], plan.get("group") or []
    need = {_alias(m.get("of")) for m in out} - {None}
    tested = {_alias((m.get("if") or {}).get("attr")) for m in out} - {None}
    grouped = {_alias(d.get("year") or d.get("code_class")) for d in dims} - {None}
    # a detail only tested by a share or patients_if is optional: the rows without it still count
    optional = {a for a in tested - need - grouped if a in ("rank", "route") or a in coded(r["type"])}
    # no BIND anywhere (see _record): computed attributes, group keys and per-row patients are expressions in place
    lines = _record(name, r, sorted((need | tested | grouped) - optional), code_node=any(d.get("code_class") == name for d in dims),
                    inline=True)
    if _stays(plan):  # the row's stay, which _stay_rows tests for the stay_has records
        lines.append(f"?{name} sphn:hasAdministrativeCase ?{name}_case .")
    for a in sorted(optional):
        prop, kind = (spec[a], "code") if a in ("rank", "route") else coded(r["type"])[a]
        var = f"?{name}_{a}"
        lines.append(f"OPTIONAL {{ ?{name} {prop} {var}_code . {var}_code rdf:type {var} . }}" if kind in ("code", "class") else
                     f"OPTIONAL {{ ?{name} {prop} {var} . }}")
    top, after, head, group, sources, also = [], [], [], [], [], []
    for d in dims:
        item, by, src = _group_key(d, name, r, out, lines, top, after)
        head.append(item), group.append(by), sources.append(src)
    for m in out:
        item, alone = _measure(m, name, recs, out)
        if alone:
            also.append((m["name"], m["if"]))
        head.append(item)
    tail = ("\nGROUP BY " + " ".join(group) if group else "") + _order_limit(plan)
    where = [*top, *_graph(lines), *after]
    if _stays(plan):
        if also:
            raise Unsupported('stay_has next to "patients_if": {"has": ...}: free generation')
        used = {"hours": ("start", "end"), "days": ("start", "end"), "in_stay": ("start", "admission", "discharge")}
        kept = {f"?{name}_{v}" for a in need | tested | grouped for v in used.get(a, (a,))}
        where = _stay_rows(plan, name, where, sorted(kept | {s for s in sources if not s.endswith("_stays")}))
    if also:  # one UNION branch per count, none with an OPTIONAL: the rows (DISTINCT ?g and the group keys); per test
        # (patients_if) the rows meeting it, its detail required; per "also has" that record's patients (once, first),
        # then their rows. An OPTIONAL detail next to the rows took 30+ s (organizers' example 8: 0.05 s without it)
        if any(m["agg"] not in ("patients", "patients_if") for m in out):  # one row per record: the UNION takes 30+ s too
            raise Unsupported('"patients_if": {"has": ...} next to record measures: free generation')

        def rows(test=None):  # the rows' pattern without the optional details, but the tested one required, and the test
            a = _alias((test or {}).get("attr"))
            mine = lambda line: a is not None and re.search(rf"\?{name}_{a}(?!\w)", line)
            kept = [re.sub(r"^OPTIONAL \{\s*(.*?)\s*\}$", r"\1", line, flags=re.S) for line in lines
                    if not line.startswith("OPTIONAL") or mine(line)]
            return [*top, *_graph(kept), *after, *([f"FILTER ({_test_expr(test, recs=recs, inline=True)})"] if a else [])]

        def branch(cols, pattern):
            return _sub("SELECT DISTINCT " + " ".join(cols), pattern)

        def first(other):  # the other record's patients
            return branch(["?g"], _graph(_record(other, recs[other], inline=True)))
        branches = [branch(["?g", *sources], rows())] + [
            branch([*sources, f"(?g AS ?{n}_patient)"], [*first(c["has"]), *rows()] if "has" in c else rows(c)) for n, c in also]
        where = _union(branches)
    if group and group != ["?sex"] and not also:  # the rows grouped by plain variables first (_keyed)
        where = _keyed(where, head, group, sources, plain=not (_stays(plan) or also))
    return _wrap(_query("SELECT " + " ".join(head), where, tail), unsafe)


def _alias(attr):
    """The attribute of "record.attribute" (None for a bare record name or nothing)."""
    return str(attr).split(".")[1] if "." in str(attr or "") else None


def _group_key(d, name, r, out, lines, top, after):
    """One group key of an analytic plan over the rows of record `name`: (SELECT item, GROUP BY item, the variable it
    reads). The triples and FILTERs it needs go to lines (the row pattern), top (before it) and after (after it)."""
    spec = RECORDS[r["type"]]
    if "code_class" in d:
        alias = _alias(d["code_class"])
        cls = f"?{name}_{alias}" if alias else f"?{name}_class"
        if not alias:
            lines.append(f"?{name}_code rdf:type {cls} .")
        kind = "code" if alias in (None, "rank", "route") else attr_kind(r["type"], alias)
        ns = d.get("ns") or ((spec.get("systems") or ("snomed",))[0] if not alias else "snomed" if alias in ("rank", "route") else None)
        if ns:
            after.append(f'FILTER (STRSTARTS(STR({cls}), "{SC.PREFIXES[ns]}"))')
        elif kind == "code":  # a code's class from any terminology, not the generic SPHN code classes
            after.append(f'FILTER (!STRSTARTS(STR({cls}), "{SC.NS}"))')
        elif kind == "class":  # a linked record's SPHN class, not the generic one (organizers' example 8)
            generic = CODED[r["type"]][alias][2]
            after.append(f'FILTER (STRSTARTS(STR({cls}), "{SC.NS}") && {cls} != {generic})')
        var = f"{cls}_id" if alias else f"?{name}_code_id"
        key, src = f'REPLACE(STR({cls}), "^.*[/#]", "")', cls
        if d.get("match"):  # a SPARQL string: backslashes and quotes escaped
            after.append(f'FILTER (REGEX({key}, "{d["match"].replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"))')
        if S.CODE_IRI and not _versioned(r["type"], alias, ns, kind):  # the class IRI itself, as the organizers return it
            return cls, cls, src
        if ((ns and ns not in VERSIONED) or kind in ("class", "value")) and any(m.get("agg") == "records" for m in out):
            # one code per class, records and patients counted: grouped by the class itself, its code shown (the
            # code as the key next to two DISTINCT counts: Virtuoso's "Max row length is exceeded" on 100k
            # patients, organizers' example 7; 1.2 s this way)
            return f"({key} AS {var})", cls, src
    elif "stay_has" in d:  # whether the row's stay has the record too (its count per row: _stay_rows)
        var, src = f"?stay_has_{d['stay_has']}", f"?{d['stay_has']}_stays"
        key = f"{src} > 0"
    elif d.get("sex"):
        top.append("VALUES (?sex_class ?sex) { " + " ".join(f'({c} "{label}")' for c, label in SEX) + " }")
        lines.extend(["?sex_code rdf:type ?sex_class .", "?sex_record sphn:hasCode ?sex_code .",
                      "?sex_record rdf:type sphn:AdministrativeSex ."])
        var = key = src = "?sex"
    else:
        var, src = f"?{name}_year", f"?{name}_{d['year'].split('.')[1]}"
        key = f"YEAR({src})"
    return var, (var if key == var else f"({key} AS {var})"), src


def _versioned(type_, alias, ns, kind):
    """Whether a grouped code may come from a year-versioned system (ICD-10-GM, CHOP): grouped by its short name then,
    which merges a code across its yearly versions; a value-set individual or a linked record's class never is."""
    if kind in ("class", "value"):
        return False
    if ns:
        return ns in VERSIONED
    systems = (CODED_VALUES.get(type_, {}).get(alias) or {}).get("systems") or \
        (GENERIC.get(type_, {}).get(alias, ()) + (None, None, ()))[2] or ()
    return not systems or bool(set(systems) & VERSIONED)


def _measure(m, name, recs, out):
    """One output of an analytic plan over the rows of record `name`: (SELECT item, whether it is counted in a UNION
    branch of its own, see _analytic)."""
    agg, n = m["agg"], m["name"]
    if agg == "patients":
        return f"(COUNT(DISTINCT ?g) AS ?{n})", False
    if agg == "records":
        return f"(COUNT(DISTINCT ?{name}) AS ?{n})", False
    if agg in ("first", "last"):
        return f"({'MIN' if agg == 'first' else 'MAX'}(?{name}_date) AS ?{n})", False
    if agg in ("avg", "min", "max", "sum"):
        a = m["of"].split(".")[1]
        e = f"{agg.upper()}({_computed(name, a) or f'?{name}_{a}'})"
        return (f"(ROUND({e}) AS ?{n})" if m.get("round") else f"({e} AS ?{n})"), False
    if "stay_has" in (m.get("if") or {}):  # the rows whose stay has the record (its count per row: _stay_rows)
        flag = f"?{m['if']['stay_has']}_stays > 0"
        return (f"(ROUND(1000.0 * SUM(IF({flag}, 1, 0)) / COUNT(?{name})) / 10 AS ?{n})" if agg == "share" else
                f"(COUNT(DISTINCT IF({flag}, ?g, ?{n}_none)) AS ?{n})"), False
    if agg == "share":
        test = _test_expr(m["if"], guard=True, recs=recs, inline=True)
        return f"(ROUND(1000.0 * SUM(IF({test}, 1, 0)) / COUNT(?{name})) / 10 AS ?{n})", False
    if "has" in m["if"] or any("has" in (x.get("if") or {}) for x in out if x["agg"] == "patients_if"):
        # patients who also have another record (organizers' examples 3 and 8), and next to them every patients_if:
        # a UNION branch below each (an OPTIONAL join took over 30 s on 5k patients, an OPTIONAL detail too)
        return f"(COUNT(DISTINCT ?{n}_patient) AS ?{n})", True
    # patients_if: the patient, only on rows meeting the condition (an unbound IF counts nobody)
    return f"(COUNT(DISTINCT IF({_test_expr(m['if'], guard=True, recs=recs, inline=True)}, ?g, ?{n}_none)) AS ?{n})", False


def _keyed(where, head, group, sources, plain):
    """The rows of a query grouped by an expression key (a code class without its year version, a year, whether the
    stay has a record), first grouped by plain variables in a sub-query: GROUP BY (REPLACE(...) AS ?k) straight over
    the rows put every row in a group of its own on the 100k-patient data (organizers' example 6: 70,130 rows, not 4,
    in 72 s; drug routes: 277,127, not 101; with or without DEFINE sql:select-option "order"), the same key over
    the grouped rows is right (4 rows, 0.6 s). plain: the rows are a pattern (no sub-query), grouped by all its
    variables, so a row keeps its weight in averages, sums and shares; else by the variables the outputs and keys use
    (the stay counts and the "also has" branches are distinct rows already)."""
    names = lambda text: list(dict.fromkeys(re.findall(r"\?\w+", text)))
    if plain:
        cols = names("\n".join(where))
    else:
        made = set(re.findall(r"\bAS (\?\w+)\)", " ".join(head))) | {g.rsplit(" AS ", 1)[1].rstrip(")") for g in group if g.startswith("(")}
        cols = [v for v in names(" ".join([*head, *sources])) if v not in made and not v.endswith("_none")]
    return _sub("SELECT " + " ".join(cols), where, "GROUP BY " + " ".join(cols))


def _stay_rows(plan, name, where, kept):
    """The unit's rows (where: its pattern, binding ?<name>_case), one per record, each with the number of times its
    stay has each stay_has record b (?<b>_stays): a UNION of the rows and, per b, b's stays (DISTINCT ?g ?case,
    computed first) joined to the rows, grouped by the row. No OPTIONAL join (30+ s), no BIND."""
    recs, cols = plan["records"], " ".join(["?g", f"?{name}", *kept])
    branches = [_sub(f"SELECT DISTINCT {cols}", where)]
    for b in _stays(plan):
        stay = _record(b, recs[b], inline=True) + [f"?{b} sphn:hasAdministrativeCase ?{name}_case ."]
        first = _sub(f"SELECT DISTINCT ?g ?{name}_case", _graph(stay))
        branches.append(_sub(f"SELECT DISTINCT {cols} (?{name}_case AS ?{b}_stay)", [*first, *where]))
    counts = " ".join(f"(COUNT(?{b}_stay) AS ?{b}_stays)" for b in _stays(plan))
    return _sub(f"SELECT {cols} {counts}", _union(branches), f"GROUP BY {cols}")


def _undef(body):
    """The query with its ?<name>_none variables (an IF's else branch, never bound, so COUNT and AVG skip that row) in
    a VALUES row of UNDEF at the end of the outer WHERE: the same rows, and compliance.validate's unbound-variable check
    accepts them. (After the query, Virtuoso refuses it: "No column ..._none".)"""
    none, lines = list(dict.fromkeys(re.findall(r"\?\w+_none\b", body))), body.split("\n")
    if not none or "WHERE {" not in lines or "}" not in lines[lines.index("WHERE {"):]:
        return body
    end = lines.index("}", lines.index("WHERE {"))
    return "\n".join([*lines[:end], f"    VALUES ({' '.join(none)}) {{ ({' '.join('UNDEF' for _ in none)}) }}", *lines[end:]])


def _with_other(c):
    """The cohort with each "!=" compare also requiring its other record ({"has": b})."""
    if not isinstance(c, dict):
        return c
    if isinstance(c.get("compare"), dict) and c["compare"].get("op") == "!=":
        return {"and": [{"has": str(c["compare"].get("right", "")).split(".")[0]}, c]}
    for k in ("and", "or"):
        if isinstance(c.get(k), list):
            return {k: [_with_other(x) for x in c[k]]}
    return {"not": _with_other(c["not"])} if "not" in c else c


def compile(plan):
    """The query body for a plan that check() accepts (no PREFIX/DEFINE lines: prompt.finish adds them)."""
    if plan.get("route") != "plan" or (plan.get("unsafe") or {}).get("other"):
        raise Unsupported("free generation")
    if plan.get("cohort"):
        plan = {**plan, "cohort": _with_other(plan["cohort"])}
    unsafe, out = plan.get("unsafe") or {}, plan.get("output") or []
    if plan.get("group") or plan.get("order") or plan.get("limit") or any(m.get("agg") in (*V2_AGGS, "gap") for m in out):
        return _undef(_analytic(plan))
    if unsafe.get("whole"):  # whole-graph match: kit count-triples / organizers' example 13 (triples and patients)
        patients = "".join(f" (COUNT(DISTINCT ?g) AS ?{m['name']})" for m in out if m["agg"] == "patients")
        body = (f"SELECT (COUNT(*) AS ?triples){patients}\nWHERE {{\n    GRAPH ?g {{ ?s ?p ?o }}\n}}" if unsafe["whole"] == "triples" else
                "SELECT ?type (COUNT(?s) AS ?records)\nWHERE {\n    GRAPH ?g { ?s rdf:type ?type }\n}\nGROUP BY ?type")
        return _wrap(body, unsafe)
    by_patient = {m["agg"] for m in out} <= {"patients"} and not unsafe.get("rows") and not unsafe.get("per_patient")
    recs = plan.get("records") or {}
    if unsafe.get("form") in ("CONSTRUCT", "DELETE", "INSERT"):
        if len(recs) != 1:
            raise Unsupported(f"{unsafe['form']}: one record type per plan in this version")
        name, r = next(iter(recs.items()))
        lines = _graph(_record(name, r), [f"?{name} ?p ?o ."])
        head = {"CONSTRUCT": f"CONSTRUCT {{ ?{name} ?p ?o }}", "DELETE": f"DELETE {{ GRAPH ?g {{ ?{name} ?p ?o }} }}",
                "INSERT": f"INSERT {{ GRAPH ?g {{ ?{name} ?p ?o }} }}"}[unsafe["form"]]
        return _wrap(_query(head, lines), unsafe)
    return _wrap(_query(*(_patients_query(plan) if by_patient else _records_query(plan))), unsafe)


def _patients_query(plan):
    """(head, WHERE lines) of a query about the cohort's patients: their count, their identifiers (unsafe expose),
    DESCRIBE or ASK."""
    recs, cohort, out, unsafe = plan.get("records") or {}, plan.get("cohort"), plan.get("output") or [], plan.get("unsafe") or {}
    simple = len(recs) <= 1 and (cohort is None or set(cohort) == {"has"})
    where, var = _simple(plan) if simple else _flags(plan)
    name = (out[0]["name"] if out else "countResult")
    if unsafe.get("form") == "DESCRIBE":
        return f"DESCRIBE {var}", where
    if unsafe.get("form") == "ASK":
        return "ASK", where
    if unsafe.get("expose"):  # the patients' identifiers, as the organizers' example 15 lists them
        where = where + ([f"GRAPH {var} {{ ?patient_subject rdf:type sphn:SubjectPseudoIdentifier . "
                          "?patient_subject sphn:hasIdentifier ?patient_id . }"] if recs else
                         [f"GRAPH ?g {{ {var} sphn:hasIdentifier ?patient_id . }}"])
    if unsafe.get("expose") == "rows":
        return "SELECT DISTINCT ?patient_id", where
    head = f"SELECT (COUNT({'DISTINCT ' if not recs or unsafe.get('expose') else ''}{var}) AS ?{name})"
    if unsafe.get("expose") == "list":
        head += ' (GROUP_CONCAT(DISTINCT ?patient_id; separator=", ") AS ?patient_ids)'
    return head, where


def _query(head, where, tail=""):
    return "\n".join([head, "WHERE {", *_indent(where), "}"]) + tail


def _wrap(body, unsafe):
    """The literal unsafe parts that act on the whole query: one patient's graph, another endpoint."""
    if unsafe.get("graph"):
        g = unsafe["graph"].strip("<>")
        g = g if g.startswith(("http://", "https://")) else ONE_PATIENT + g
        body = re.sub(r"GRAPH \?g\b", f"GRAPH <{g}>", body)
    if unsafe.get("service"):
        head, _, rest = body.partition("\nWHERE {\n")
        inner, _, tail = rest.rpartition("\n}")
        body = _query(head, [f"SERVICE <{unsafe['service'].strip('<>')}> {{", *_indent(inner.split("\n")), "}"], tail)
    return body
