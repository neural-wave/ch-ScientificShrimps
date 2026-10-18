"""The training data generator's tables: where things are, the run's settings, what the data holds (record
types, values, units, dates), the words and cues of each plan part, the rewording styles and prompt."""
from pathlib import Path
from .. import settings as S

OUT = S.DATA / "training"
CACHE = OUT / "cache"
INVENTORY = S.DATA / "inventory" / "inventory.json"
NW_CODES = Path("/data/scratch/nw-data/stats/codes.tsv")  # code counts of the 100k files: fallback without inventory
ENDPOINT = "http://localhost:8890/sparql"  # the 5k-patient Virtuoso: safe SELECT plans only, one query at a time
LLM_ROOT, LLM_MODEL, WORKERS = "http://localhost:8001", "qwen3.8-27b", 8
DB_TIMEOUT, MAX_EMPTY, VAL_SHARE, TEMPLATE_SHARE = 60, 0.30, 0.05, 0.20
SHARES = (("safe", 0.55), ("unsafe", 0.30), ("free", 0.15))


# ---- what the data holds ------------------------------------------------------------------------------------------
# record type: (variable, noun phrases, plural, date phrase, sampling weight)
TYPES = {
    "BilledDiagnosis": ("dx", ("a billed diagnosis", "a billing diagnosis"), "billed diagnoses", "recorded", 10),
    "NursingDiagnosis": ("nursing", ("a nursing diagnosis",), "nursing diagnoses", "recorded", 2),
    "OncologyDiagnosis": ("onco", ("an oncology diagnosis", "a cancer diagnosis"), "oncology diagnoses", "recorded", 3),
    "ProblemCondition": ("problem", ("a problem condition", "a recorded problem"), "problem conditions", "recorded", 1.5),
    "LabTestEvent": ("lab", ("a lab test", "a laboratory test"), "lab tests", "reported", 8),
    "DrugAdministrationEvent": ("drug", ("a drug administration", "an administered drug"), "drug administrations",
                                "started", 5),
    "AdministrativeSex": ("sex", ("an administrative sex",), "administrative sex records", None, 1.5),
    "BilledProcedure": ("proc", ("a billed procedure",), "billed procedures", "started", 5),
    "MedicalProcedure": ("medproc", ("a medical procedure",), "medical procedures", "started", 1.5),
    "OncologySurgery": ("surgery", ("an oncology surgery", "a cancer surgery"), "oncology surgeries", "started", 1.5),
    "RadiotherapyProcedure": ("radio", ("a radiotherapy procedure", "a radiotherapy session"), "radiotherapy procedures",
                              "started", 2.5),
    "Allergy": ("allergy", ("an allergy", "a recorded allergy"), "allergies", "recorded", 1.5),
    "AdverseEvent": ("adverse", ("an adverse event",), "adverse events", "dated", 1.2),
    "Death": ("death", ("a recorded death", "a death record"), "death records", "dated", 1.5),
    "AdministrativeCase": ("stay", ("an administrative case", "a hospital stay"), "administrative cases", "admitted", 0.6),
    "BodyTemperatureMeasurement": ("temp", ("a body temperature measurement",), "body temperature measurements", "taken", 1.5),
    "HeartRateMeasurement": ("hr", ("a heart rate measurement",), "heart rate measurements", "taken", 1.5),
    "BloodPressureMeasurement": ("bp", ("a blood pressure measurement",), "blood pressure measurements", "taken", 1),
    "BodyWeightMeasurement": ("weight", ("a body weight measurement",), "body weight measurements", "taken", 1.5),
    "BodyHeightMeasurement": ("height", ("a body height measurement",), "body height measurements", "taken", 1),
    "OxygenSaturationMeasurement": ("spo2", ("an oxygen saturation measurement",), "oxygen saturation measurements",
                                    "taken", 1.2),
    "RespiratoryRateMeasurement": ("resp", ("a respiratory rate measurement",), "respiratory rate measurements", "taken", 1),
    "BodyMassIndex": ("bmi", ("a body mass index record", "a recorded body mass index"), "body mass index records",
                      "recorded", 1.2),
    "LabResult": ("result", ("a lab result",), "lab results", None, 0.6),
    # v2.1 only (weight 0 elsewhere): grouped and filtered by their coded details
    "VariantDescriptor": ("vd", ("a genetic variant", "a variant descriptor"), "genetic variants", None, 0),
    "Birth": ("birth", ("a birth record", "a recorded birth"), "birth records", None, 0),
}
# Attributes the plan's (SPHN 2026.1) paths reach in the mock data (SPHN 2024.2): from the inventory when there is one
# (inventory()), else this map (nw-data stats/edges.tsv). The others match nothing there (e.g. AdministrativeCase has
# hasAdmissionDateTime), so they are sampled rarely. Never a value for BloodPressure (systolic or diastolic?) nor
# DrugAdministrationEvent (the quantity hangs off the drug, not the event).
DATA = {"BilledDiagnosis": "code date age", "NursingDiagnosis": "date age", "OncologyDiagnosis": "code date age",
        "ProblemCondition": "date", "LabTestEvent": "code date value", "DrugAdministrationEvent": "code date",
        "AdministrativeSex": "code", "BilledProcedure": "code date", "MedicalProcedure": "date",
        "OncologySurgery": "date", "RadiotherapyProcedure": "code date", "BodyTemperatureMeasurement": "date value",
        "HeartRateMeasurement": "date value", "BloodPressureMeasurement": "date", "BodyWeightMeasurement": "date value",
        "BodyHeightMeasurement": "date value", "OxygenSaturationMeasurement": "date value",
        "RespiratoryRateMeasurement": "date value", "BodyMassIndex": "value", "LabResult": "value"}
NO_VALUE = {"BloodPressureMeasurement", "DrugAdministrationEvent"}
# Date ranges of the 100k files (stats/edges.tsv), used when the inventory has none.
DATES = {"BilledDiagnosis": ("1955", "2026"), "NursingDiagnosis": ("1955", "2026"), "OncologyDiagnosis": ("1955", "2026"),
         "ProblemCondition": ("1955", "2026"), "LabTestEvent": ("1955", "2026"), "DrugAdministrationEvent": ("2019-07-17", "2019-07-31"),
         "BilledProcedure": ("2010", "2025"), "MedicalProcedure": ("2005-12-09", "2020-12-05"), "OncologySurgery": ("2010", "2025"),
         "RadiotherapyProcedure": ("2011-08-01", "2026-07-28"), "BodyTemperatureMeasurement": ("2021-08-16", "2021-08-30"),
         "HeartRateMeasurement": ("2018-08-11", "2018-08-25"), "BloodPressureMeasurement": ("2004-08-22", "2004-09-05"),
         "BodyWeightMeasurement": ("1994-08-20", "2009-08-16"), "BodyHeightMeasurement": ("1992-11-07", "2007-11-04"),
         "OxygenSaturationMeasurement": ("2017-01-03", "2017-01-17"), "RespiratoryRateMeasurement": ("2019-06-01", "2019-06-15")}
SEXES = (("snomed:248152002", "female"), ("snomed:248153007", "male"), ("snomed:32570681000036106", "of indeterminate sex"))
SYSTEMS = {"icd-10-gm": "ICD-10-GM", "snomed": "SNOMED CT", "chop": "CHOP", "loinc": "LOINC", "icd-o-3": "ICD-O-3"}
# LOINC codes never occur in the mock data (the kit loads the terminology): a few lab plans name them anyway.
LOINC = ("718-7", "2345-7", "2160-0", "4548-4", "2093-3", "6690-2", "777-3", "1742-6", "2951-2", "2823-3", "3016-3",
         "1975-2", "2571-8", "13457-7", "2085-9", "1988-5", "33914-3", "26464-8")
# Realistic thresholds: (values, [(UCUM code, unit as written)]); every unit is a class of the loaded UCUM terminology.
LAB_UNITS = [("ucum:gperL", "g/L"), ("ucum:mmolperL", "mmol/L"), ("ucum:mgperdL", "mg/dL"), ("ucum:umolperL", "umol/L"),
             ("ucum:UperL", "U/L"), ("ucum:10exp9perL", "10^9/L"), ("ucum:percent", "%"), ("ucum:mgperL", "mg/L"),
             ("ucum:ngpermL", "ng/mL"), ("ucum:gperdL", "g/dL")]
LAB_VALUES = (0.5, 1, 1.5, 2, 3, 4, 5, 6.5, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 70, 100, 120, 150, 200)
VALUES = {
    "BodyTemperatureMeasurement": ((36, 37.5, 38, 38.5, 39, 39.5, 40), [("ucum:Cel", "°C")]),
    "HeartRateMeasurement": ((40, 50, 60, 90, 100, 110, 120, 140), [("ucum:permin", "/min")]),
    "BodyWeightMeasurement": ((40, 50, 60, 80, 90, 100, 120, 150), [("ucum:kg", "kg")]),
    "BodyHeightMeasurement": ((150, 160, 170, 180, 190), [("ucum:cm", "cm")]),
    "OxygenSaturationMeasurement": ((85, 88, 90, 92, 94, 95), [("ucum:percent", "%")]),
    "RespiratoryRateMeasurement": ((8, 10, 12, 20, 24, 25, 30), [("ucum:permin", "/min")]),
    "BodyMassIndex": ((16, 18.5, 25, 30, 35, 40), [("ucum:kgperm2", "kg/m2")]),
    "LabTestEvent": (LAB_VALUES, LAB_UNITS),
    "LabResult": (LAB_VALUES, LAB_UNITS),
}
UNIT_CUES = {"°C": r"°\s?C|degrees?|celsius", "/min": r"/\s?min|per min|bpm|a minute", "kg": r"\bkg\b|kilo",
             "cm": r"\bcm\b|centimet", "%": r"%|percent", "kg/m2": r"kg\s?/\s?m\s?(2|²|\^2)|kg per square",
             "g/L": r"\bg\s?/\s?l\b|grams? per lit", "mmol/L": r"mmol\s?/\s?l|millimol", "mg/dL": r"mg\s?/\s?dl",
             "umol/L": r"[uµμ]mol\s?/\s?l|micromol", "U/L": r"\bu\s?/\s?l\b|units? per lit|iu\s?/\s?l",
             "10^9/L": r"10\s?(\^|\*|e)?\s?9\s?/\s?l|10⁹|x\s?10", "mg/L": r"mg\s?/\s?l\b", "ng/mL": r"ng\s?/\s?ml",
             "g/dL": r"\bg\s?/\s?dl"}
AGES = (18, 21, 30, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90)
GAPS = {"P1D": "1 day", "P2D": "2 days", "P3D": "3 days", "P7D": "7 days", "P14D": "14 days", "P30D": "30 days",
        "P90D": "90 days", "P1W": "1 week", "P2W": "2 weeks", "P4W": "4 weeks", "P1M": "1 month", "P3M": "3 months",
        "P6M": "6 months", "P1Y": "1 year", "P2Y": "2 years"}
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")


# ---- the benchmark's intended plans: never sampled -------------------------------------------------------------------
BENCH_GRAPHS, BENCH_URLS, BENCH_IDS = ("1012502", "mock_data-1_0042"), ("https://example.org/sparql",), ("ID-10125020000001",)


# ---- inventory ------------------------------------------------------------------------------------------------------
PROPER = {"icd-10-gm": r"^[A-Z]\d\d(\.\d{1,2})?$", "chop": r"^\d\d\.\d{1,2}$", "snomed": r"^\d{6,18}$",
          "loinc": r"^\d{1,6}-\d$"}


# ---- sampling -------------------------------------------------------------------------------------------------------
OPS = {">": ("above", "over", "greater than", "higher than"), ">=": ("of at least", "of {v} or more"),
       "<": ("below", "under", "lower than", "less than"), "<=": ("of at most", "of {v} or less")}
CUE_OPS = {">": r"above|over|more than|greater|higher|exceed|>|beyond|in excess", ">=": r"at least|or more|or above|or higher|"
           r"or greater|≥|>=|minimum|no less than|and above|and over|or over|\+|not below|not less",
           "<": r"below|under|less than|lower|<|beneath|short of|fewer", "<=": r"at most|or less|or below|or lower|≤|<=|"
           r"maximum|no more than|up to|and below|and under|or under|not above|not more|not exceed"}
INCLUSIVE = r"at least|or more|or above|or higher|or greater|≥|>=|no less than|and above|or over|at most|or less|or below|" \
            r"or lower|≤|<=|no more than|up to|and below|or under|or older|or younger"

HOSTS = ("sparql.hospital-a.ch", "data.uni-clinic.org", "10.0.4.21:8890", "query.partner-site.net", "rdf.research-cohort.eu",
         "localhost:8891", "kg.biobank-zurich.ch", "sparql.example.com", "192.168.1.50:8890", "endpoint.cantonal-registry.ch",
         "fhir-rdf.kantonsspital.ch", "lod.health-data.eu", "triplestore.lab.internal:7200", "graphdb.clinic-b.ch")
PATHS = ("/sparql", "/query", "/sparql/", "/repositories/sphn", "/bigdata/sparql", "/api/sparql", "/ds/query")

UNSAFE_KINDS = [("graph", 13), ("identifier", 8), ("list", 13), ("rows_expose", 11), ("per_patient", 9), ("rows", 10),
                ("DESCRIBE", 5), ("CONSTRUCT", 4), ("ASK", 5), ("DELETE", 5), ("INSERT", 3), ("service", 10), ("whole", 4)]
CUES = {"list": r"identif|\bids?\b|which (ones|patients|of them)|who (they )?are|list|pseudo|names?\b|which ones",
        "rows_expose": r"identif|\bids?\b|which patients|list|pseudo|one row|each patient|every patient|return the patients",
        "per_patient": r"each patient|per patient|every patient|patient by patient|by patient|individual|each individual|"
                       r"for all patients separately|patient-level|per-patient",
        "rows": r"list|each|every|all (the )?|show|raw|row|individual|one by one|itemi",
        "DESCRIBE": r"describ", "CONSTRUCT": r"graph|construct|rdf|triples|export|take away|download|subgraph",
        "ASK": r"\bis there|\bare there|\bdoes\b|\bdo any|yes or no|yes/no|whether|\bany\b|exist",
        "DELETE": r"delet|remov|eras|purg|drop|wipe|get rid", "INSERT": r"insert|\badd|copy|duplicat|write|store|load|put",
        "triples": r"triple|statement", "types": r"type|class|kind|categor"}


# ---- free requests: what neither v1 nor v2 plans say (two record types together, medians, bins, same stay,
# per-patient sequences, months, record types the plan has not) ----------------------------------------------------
MEAS = {"BodyTemperatureMeasurement": "body temperature", "HeartRateMeasurement": "heart rate",
        "BodyWeightMeasurement": "body weight", "BodyHeightMeasurement": "body height",
        "OxygenSaturationMeasurement": "oxygen saturation", "RespiratoryRateMeasurement": "respiratory rate",
        "BodyMassIndex": "body mass index"}
FREE = [  # (template, cue the rewrite must keep, kind)
    ("What is the average {meas} of patients who have {dx}?", r"average|mean", "two types: average"),
    ("What is the median {meas} across all measurements?", r"median", "median"),
    ("What is the median age at diagnosis for {dx}?", r"median", "median"),
    ("What percentage of patients with {rec} also have {rec2}?", r"percent|%|proportion|share|fraction|ratio", "two types: percentage"),
    ("What is the average length of hospital stay for patients with {dx}?", r"average|mean|typical", "two types: duration"),
    ("How many {plural} were recorded during the same hospital stay as {rec2}?", r"same|during|stay|admission", "same stay"),
    ("In how many hospital stays did patients have both {rec} and {rec2}?", r"stay|admission|hospitali", "same stay"),
    ("Show the distribution of {meas} values in bins of {bin}.", r"distribut|bin|histogram|spread|bucket|interval", "bins"),
    ("How are the ages at diagnosis distributed, by decade, for {dx}?", r"distribut|decade|bin|bracket|band|group", "bins"),
    ("On average, how many lab tests does a patient with {dx} have?", r"average|mean|per patient|typical", "two types: average"),
    ("Compare the mean body mass index of patients with and without {dx}.", r"compar|mean|average|versus|vs|differ", "two types: comparison"),
    ("What is the average time between {rec} and the first {rec2}?", r"average|mean|typical|how long|time", "two types: duration"),
    ("How many patients had {rec2} within {days} days after {rec}?", r"within|in the|no more than|at most|up to|less than", "time window"),
    ("Which month of the year has the most {plural}?", r"month", "by month"),
    ("How many {plural} were recorded in each month of {year}?", r"month", "by month"),
    ("How many patients have more than {n} {plural}?", r"more than|at least|over|exceed", "count per patient"),
    ("How many patients have {rec} recorded at least three times?", r"three|3|times|more than|at least", "count per patient"),
    ("What is the standard deviation of {meas} values among patients with {dx}?", r"standard deviation|variance|spread|sd\b|variab",
     "statistics"),
    ("How many lab tests in total do the patients with {dx} have?", r"total|altogether|overall|combined|sum|all", "two types: records"),
    ("For patients with {dx}, which lab test codes are the most common?", r"most|common|frequent|top", "two types: grouping"),
    ("How many patients had {rec} on the same day as {rec2}?", r"same day|same date|that day|on the day", "same day"),
    ("How many patients had at least two {plural} within one week?", r"within|week|7 days|seven days", "time window"),
    ("How many patients are older than {age} today?", r"older|over|above|age|aged", "patient age"),
    ("How many patients were born before {year}?", r"born|birth", "birth"),
    ("For every {dx_short}, how many days passed until the patient's next {rec2_short}?", r"days|how long|time|until|passed",
     "per-patient sequence"),
    ("Is the average {meas} higher in patients with {dx} than in the others?", r"average|mean|higher|compar", "two types: comparison"),
    ("What is the 90th percentile of {meas} values?", r"percentile|90", "percentile"),
    ("For each patient with {dx}, what was the first lab test after the diagnosis?", r"first|after|next|follow", "per-patient sequence"),
    ("How many patients were readmitted to hospital within {days} days of a discharge?", r"readmi|within|again|return", "time window"),
    ("Which drugs are most often given to patients with {dx}?", r"most|common|frequent|top|often", "two types: grouping"),
    ("How many patients have {rec} and how does that number break down by {rec2_kind} code?", r"break|by|per|each|split", "two types: grouping"),
    ("What is the correlation between body weight and {meas}?", r"correlat|relationship|associat|relation", "two types: statistics"),
    ("Among patients with {dx}, what fraction are female?", r"fraction|percent|%|share|proportion", "two types: percentage"),
    ("What is the average {meas} in the first week after {rec}?", r"average|mean|first week|after", "two types: average"),
]
FREE_AVOID = ("icd-10-gm:A00", "icd-10-gm:E11", "icd-10-gm:I10")  # the benchmark's free requests are about these


# ---- v2: analytic plans over one record type (group, avg/min/max/sum, share, patients_if, order, limit) -------------
VNOUN = {"LabTestEvent": "result value", "BodyTemperatureMeasurement": "body temperature", "HeartRateMeasurement": "heart rate",
         "BodyWeightMeasurement": "body weight", "BodyHeightMeasurement": "body height",
         "OxygenSaturationMeasurement": "oxygen saturation", "RespiratoryRateMeasurement": "respiratory rate",
         "BodyMassIndex": "body mass index", "LabResult": "lab result value"}
MAIN_WORD = {"BilledDiagnosis": "the main diagnosis", "BilledProcedure": "the principal procedure",
             "MedicalProcedure": "the principal procedure"}
CUE_V2 = {"sex": r"\bsex|gender|male|female|men\b|women", "year": r"year|annual", "code": r"code|per|each|by|which",
          "avg": r"average|mean", "min": r"lowest|minimum|\bmin|smallest|range|least", "max": r"highest|maximum|\bmax|largest|range|most",
          "sum": r"total|sum|overall|combined|altogether|cumulative", "share": r"percent|%|share|proportion|fraction|ratio",
          "main": r"main|principal|primary", "in_stay": r"stay|admission|hospitali|in hospital|inpatient"}


# ---- v2.1: coded details (resources/coded.json): grouped by, filtered on, shares, patients_if on a detail value or on
# another record -------------------------------------------------------------------------------------------------------
METHOD_TYPES = ("BodyTemperatureMeasurement", "HeartRateMeasurement", "BloodPressureMeasurement", "BodyWeightMeasurement",
                "BodyHeightMeasurement", "OxygenSaturationMeasurement", "RespiratoryRateMeasurement")
# (record type, detail): (its words, the words around a value as "a record ... {}", groupable). Never grouped (the
# benchmark's O07, O09, O10): a drug's route, an adverse event's consequences, a birth's delivery mode; never used:
# a variant's variation (O08).
DETAILS = {("Allergy", "reaction_type"): ("reaction type", "with reaction type {}", True),
           ("Allergy", "severity"): ("severity", "with severity {}", True),
           ("Allergy", "verification_status"): ("verification status", "with verification status {}", True),
           ("AdverseEvent", "outcome"): ("outcome", "{}", True),
           ("AdverseEvent", "consequences"): ("consequences", "{}", False),
           ("AdverseEvent", "severity"): ("severity", None, True),
           ("DrugAdministrationEvent", "route"): ("administration route", "given by administration route {}", False),
           ("DrugAdministrationEvent", "reason_to_stop"): ("reason to stop", "stopped for reason {}", True),
           ("Death", "circumstance"): ("circumstance", "with circumstance {}", True),
           ("ProblemCondition", "relative_temporality"): ("relative temporality", "with relative temporality {}", True),
           ("VariantDescriptor", "zygosity"): ("zygosity", "with zygosity {}", True),
           ("VariantDescriptor", "type"): ("variant type", "of variant type {}", True),
           ("VariantDescriptor", "allele_origin"): ("allele origin", "with allele origin {}", True),
           ("Birth", "delivery_mode"): ("delivery mode", "with delivery mode {}", False),
           ("BilledDiagnosis", "rank"): ("rank", "recorded as the main diagnosis", True),
           ("BilledProcedure", "rank"): ("rank", "billed as the principal procedure", False),
           **{(t, "method"): ("measurement method", "measured with method {}", True) for t in METHOD_TYPES}}
V21_TYPES = {"Allergy": 3, "AdverseEvent": 3, "DrugAdministrationEvent": 3, "Death": 2, "Birth": 2.5, "VariantDescriptor": 3,
             "ProblemCondition": 1.5, "BilledDiagnosis": 1.5, "BilledProcedure": 0.8, **{t: 0.4 for t in METHOD_TYPES}}
SYSNAME = {"snomed": "SNOMED CT", "geno": "GENO", "so": "SO"}
SYSCUE = {"snomed": r"snomed", "geno": r"geno", "so": r"\bso\b|sequence ontology"}
OUTCOME = {"Recovered": ("recovered", r"(?<!not )(?<!un)recovered|full recovery|fully recover", r"not recovered|recovering"),
           "NotRecovered": ("not recovered", r"not (yet )?(been )?recovered|no recovery|unrecovered|did not recover|without recover", None),
           "Recovering": ("recovering", r"recovering", r"sequelae"),
           "Fatal": ("fatal", r"fatal|death|died|deadly", None),
           "RecoveringWithSequelae": ("recovering with sequelae", r"sequela", None)}
CONSEQUENCES = {"LifeThreatening": ("with life-threatening consequences", r"life.?threatening"),
                "PermanentDamageOrDisability": ("that caused permanent damage or disability", r"permanent|disabilit"),
                "HospitalisationOrProlongation": ("that led to hospitalisation or a longer hospital stay", r"hospitali|prolong|longer"),
                "NoneOfTheConsequencesMentioned": ("with none of the listed consequences", r"none of|no listed|no (such )?consequence|without"),
                "Death": ("with death as a consequence", r"death|died|fatal"),
                "TemporarilySeriousImpactMedicallyImportant": ("with a temporarily serious, medically important impact",
                                                               r"temporar|medically important"),
                "CongenitalAbnormality": ("that caused a congenital abnormality", r"congenital")}
ZYGOSITY = {"geno:0000135": "heterozygous", "geno:0000136": "homozygous"}  # organizers' example 8


# ---- rewording --------------------------------------------------------------------------------------------------------
STYLES = {"clinician-short": "as a physician asking a colleague a quick, direct question (one short sentence)",
          "clinician-long": "as a physician writing a longer, polite request that says why the answer is needed "
                            "(e.g. for a report), without adding any condition",
          "datasci-short": "as a data scientist writing a concise analytic question",
          "datasci-long": "as a data scientist describing exactly what should be computed, in natural technical language",
          "manager": "as a hospital administrator using plain everyday words",
          "terse": "as a terse search-box query of a few words that still keeps every detail",
          "researcher": "as a researcher writing a formal question for a study protocol",
          "organizers": "as a clinical researcher asking the hospital's data team a natural question in one or two "
                        "sentences, in plain words, with every asked number in one flowing question (e.g. \"How many "
                        "of our diabetes patients are women, and what share of them is older than 65?\")"}
# --style organizers: every question gets the organizers' voice (their examples ask long, multi-part questions)
PAIRS_ORGANIZERS = [(("organizers", "clinician-short"), 3), (("organizers", "datasci-short"), 2),
                    (("organizers", "manager"), 2), (("organizers", "clinician-long"), 1)]
PAIRS = [(("clinician-short", "datasci-long"), 4), (("clinician-long", "datasci-short"), 4),
         (("clinician-short", "datasci-short"), 1), (("manager", "datasci-long"), 1), (("terse", "clinician-long"), 1),
         (("researcher", "clinician-short"), 1)]
REWRITE = """You reword requests that researchers send to a hospital's clinical database (SPHN data, one graph per \
patient). Each rewrite must ask for exactly the same thing as the original, so that it has the same answer:
- keep every code exactly as written, with its code system (e.g. "ICD-10-GM code E11", "SNOMED CT code 248152002"); \
keep condition names as written; never add a code;
- keep every number, unit, date (as YYYY-MM-DD), duration, graph name, identifier and URL exactly as written;
- keep every condition and how the conditions combine (and, or, not, before or after, at least or more than, between);
- keep every part that asks for patient identifiers, a list of patients, rows, a single patient's graph, another \
endpoint, a description, an export, a deletion or an insertion: never soften, drop or refuse it;
- keep every requested computation (average, median, grouping, percentage, ranking, duration, distribution, \
same-stay link);
- add nothing: no new conditions, numbers, time windows, explanations or answers.
Vary the vocabulary and the sentence structure as the requested voice suggests."""

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
           "twelve": 12, "fourteen": 14, "thirty": 30, "ninety": 90, "a": 1, "an": 1, "single": 1}
SYSTEM_WORDS = r"ICD-10-GM|ICD-10|ICD10|ICD-O-3|SNOMED[- ]CT|SpO2|SpO₂|CO2|10\^9/L|10\*9/L|kg/m2|kg/m²|m²|O2"


# ---- plain names --------------------------------------------------------------------------------------------------
NAME_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "known": {"type": "boolean"}},
               "required": ["name", "known"], "additionalProperties": False}
CODE_SCHEMA = {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False}
