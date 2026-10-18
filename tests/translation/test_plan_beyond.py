"""Two shapes no plan can say go to free generation before any plan call (plan.BEYOND): an average over per-patient
counts (per_patient lists each patient's count, no output averages them) and a count of patient identifiers (the 9B
lists them instead). Ordinary counts, averages and per-patient counts keep their plan: most of the negatives below
are worded as plans of the training data are."""
import pytest
from nl2q.translation import plan as P

AVERAGE_PER_PATIENT = [
    "What is the average number of body height measurement records per patient identifier?",
    "What is the mean number of lab tests per patient with a billed diagnosis of ICD-10-GM code I10?",
    "What is the average number of heart rate measurements recorded on or after 1 January 2000, calculated per "
    "patient identifier by counting the measurements for each patient and then averaging those counts?",
    "What is the average count of drug administrations for each patient?",
    "Average lab tests per patient, billed diagnosis ICD-10-GM K21",
    "On average, how many lab tests does a patient with a billed diagnosis have?",
    "How many billed procedures does each patient have on average?",
    "How many diagnoses per patient on average?",
]
COUNT_IDENTIFIERS = [
    "How many distinct patient pseudo-identifiers have a heart rate measurement?",
    "How many patient pseudo identifiers have an allergy recorded after 2010?",
    "How many distinct patient pseudo‑identifiers have a tumor specimen record?",
    "How many unique patient IDs have a billed procedure?",
    "What is the number of distinct patient pseudo-identifiers with a lab test?",
    "Count the distinct subject pseudo-identifiers linked to a body weight measurement.",
]
PLANNED = [
    "How many patients have a billed diagnosis with ICD-10-GM code I10?",
    "How many distinct patients have a heart rate measurement recorded on or after 1 January 2000?",
    "What is the average value of the body weight measurements?",
    "What is the average body temperature value across all the body temperature measurement records?",
    "What is the number of lab test records per year?",
    "Count the billed diagnoses per patient where the ICD-10-GM code is A00.0.",
    "Calculate the number of lab tests with LOINC 777-3 per patient.",
    "How old are patients on average across billed diagnoses, rounded to the nearest whole number?",
    "How many days on average pass between a patient's first ICD-10-GM code A00.9 diagnosis and their next medical "
    "procedure?",
    "On average, how many hours do the drug administrations (SNOMED CT code 78588006) last?",
    "Calculate the count of distinct patient identifiers where the adverse event outcome is fatal.",
    "Compute the total count of blood pressure measurement records and the number of unique patient identifiers "
    "associated with those records.",
    "How many patients have a nursing diagnosis, and what are their pseudo identifiers?",
    "How many patients have pseudo identifiers for a CHOP code 00.0 procedure started after 2015?",
    "Retrieve the count and pseudo identifiers for patients with a billed procedure.",
    "How many distinct sample record identifiers have a recorded fixation type?",
    "How many patients with the identifier FID-8405862 have a hospital stay admitted on or after 2007-08-15?",
]


def _no_plan_call(monkeypatch):
    monkeypatch.setattr(P, "write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no plan call")))


@pytest.mark.parametrize("text", AVERAGE_PER_PATIENT)
def test_an_average_count_per_patient_goes_free(text, monkeypatch):
    _no_plan_call(monkeypatch)
    query, note = P.query(text, {})
    assert query is None and "an average count per patient" in note


@pytest.mark.parametrize("text", COUNT_IDENTIFIERS)
def test_a_count_of_patient_identifiers_goes_free(text, monkeypatch):
    _no_plan_call(monkeypatch)
    query, note = P.query(text, {})
    assert query is None and "a count of patient identifiers" in note


@pytest.mark.parametrize("text", PLANNED)
def test_ordinary_counts_averages_and_identifiers_keep_their_plan(text):
    assert P.beyond(text) is None
