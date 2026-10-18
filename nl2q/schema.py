"""The schema config (written by `python -m nl2q.tools.adapt`), shared by both layers: the facts the compliance check
reads (identifier, code, literal and value properties, the patient link, every declared term), the prefixes, and the
raw config (CFG) from which the translation layer builds its prompt (header, index, blocks)."""
import json
from rdflib import URIRef
from . import settings as S

CFG = json.loads(S.SCHEMA.read_text())
_iris = lambda k: frozenset(map(URIRef, CFG[k]))
NS = CFG["schema_ns"]                     # sphn: classes and properties
PREFIXES = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "owl": "http://www.w3.org/2002/07/owl#", "bif": "bif:", **CFG["prefixes"]}
LINK = URIRef(CFG["link"])                # record -> patient (sphn:hasSubjectPseudoIdentifier)
PATIENT_CLASS = URIRef(CFG["patient_class"])
ID_PROPS = _iris("id_props")              # identifier values: never released (code identifiers excepted)
CODE_PROPS = _iris("code_props")          # their objects are code nodes
LITERAL_PROPS = _iris("literal_props")    # their objects are values (dates, numbers, strings)
VALUE_PROPS = _iris("value_props")        # their objects are value-set individuals (sphn-individual:Fatal, ...)
TERMS = frozenset(CFG["terms"])           # every sphn: term the schema declares
