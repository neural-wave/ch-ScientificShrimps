"""Words for the parts of a plan: units as written, codes, record nouns, the "patients who ..." clause and
the count question around it."""
from .tables import TYPES, SYSTEMS


def ucum_text(code):
    """A UCUM code as written ("ucum:10exp9perL" -> "10*9/L"), from SPHN's spelling of its symbols."""
    s = code.split(":", 1)[1]
    for a, b in (("percent", "%"), ("sbl", "["), ("sbr", "]"), ("cbl", "{"), ("cbr", "}"), ("rbl", "("), ("rbr", ")"),
                 ("dot", "."), ("exp", "*"), ("per", "/")):
        s = s.replace(a, b)
    return {"Cel": "°C", "mm[Hg]": "mmHg"}.get(s, s)


def code_phrase(rng, codes, keep, names):
    """The words for a record's codes ("with ICD-10-GM code E11", or the plain name when the generator knows it)."""
    system = codes[0].split(":")[0]
    locals_ = [c.split(":", 1)[1] for c in codes]
    if len(codes) == 1 and codes[0] in names and rng.random() < 0.45:
        keep.names.add(names[codes[0]])
        return rng.choice(("of {}", "for {}", "with {}")).format(names[codes[0]]), True
    keep.exact |= set(locals_)
    keep.cue("code system", {"icd-10-gm": r"icd|", "snomed": r"snomed", "chop": r"chop", "loinc": r"loinc"}.get(system, ""))
    shown = " or ".join(locals_) if len(locals_) < 3 else ", ".join(locals_[:-1]) + " or " + locals_[-1]
    sys_ = SYSTEMS.get(system, system)
    word = "code" if len(codes) == 1 else "codes"
    return rng.choice((f"with {sys_} {word} {shown}", f"coded {sys_} {shown}", f"({sys_} {word} {shown})")), False


def noun(rng, rtype, plural=False, article=True):
    phrases = TYPES[rtype][1]
    if plural:
        return TYPES[rtype][2]
    n = rng.choice(phrases)
    return n if article else n.split(" ", 1)[1]


def describe(rng, rtype, words, plural=False, article=True):
    """'a billed diagnosis with ICD-10-GM code E11 recorded before 2020' (or the plural)."""
    if rtype == "AdministrativeSex":
        word, extra = words
        return f"{'patients' if plural else 'a patient'} {('who are ' if plural else 'who is ')}{word}" + (f" {extra}" if extra else "")
    return (noun(rng, rtype, plural, article) + (" " + words[1] if words[1] else "")).strip()


def phrase_has(rng, rtype, words):
    """'have a billed diagnosis ...' / 'are female'."""
    if rtype == "AdministrativeSex":
        return f"are {words[0]}" + (f" {words[1]}" if words[1] else "")
    return rng.choice(("have", "had")) + " " + describe(rng, rtype, words)


def count_question(rng, clause):
    return rng.choice((f"How many patients {clause}?", f"Count the patients who {clause}.",
                       f"What is the number of patients who {clause}?", f"How many patients in the database {clause}?"))
