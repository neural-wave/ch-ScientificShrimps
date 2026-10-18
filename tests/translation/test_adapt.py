"""tools.adapt: which property names count as identifiers (blocked except on code nodes); 2024.2 data, 2026.1 card."""
import pytest
from rdflib import URIRef
from nl2q.tools.adapt import SPHN, is_identifier, successors


def test_2024_2_pairs_in_the_data_populate_their_2026_1_successors():
    s = lambda n: URIRef(SPHN + n)
    out = successors({(s("BilledDiagnosis"), s("hasRank")): {SPHN}, (s("AdministrativeCase"), s("hasAdmissionDateTime")): {""}})
    assert out[s("BilledDiagnosis"), s("hasRankCode")] == {"http://snomed.info/id/"}
    assert (s("AdministrativeCase"), s("hasAdmission")) in out and out[s("Admission"), s("hasDateTime")] == {""}
    assert (s("BilledDiagnosis"), s("hasRank")) in out  # the old pair stays: the card shows only 2026.1 terms anyway
    assert (s("LabTestEvent"), s("hasAdmission")) not in successors({(s("LabTestEvent"), s("hasAdmissionDateTime")): {""}})


@pytest.mark.parametrize("name", ["hasIdentifier", "hasSharedIdentifier", "hasPhoneNumber", "hasAHVNumber", "hasEmailAddress",
                                  "hasPostalCode", "hasFirstName", "hasFamilyName", "hasID", "hasStreet"])
def test_identifiers(name):
    assert is_identifier(name)


@pytest.mark.parametrize("name", ["hasCode", "hasValue", "hasCodingSystemAndVersion", "hasDataProvider", "hasRecordDateTime",
                                  "hasAdministrativeCase", "hasValidity", "hasName"])
def test_not_identifiers(name):
    assert not is_identifier(name)
