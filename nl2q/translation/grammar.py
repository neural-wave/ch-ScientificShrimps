"""The syntax guard (resources/sparql.ebnf): an xgrammar EBNF grammar of the SPARQL nl2q sends to Virtuoso, without
PREFIX or DEFINE lines. Passed to vLLM's structured outputs, it lets the model write only a query that parses, alone or
in the ```sparql block the prompt asks for, and no prose. It is about syntax, not safety: unsafe queries stay writable
for the checker."""
from pathlib import Path
from .. import schema as SC

# After sphn: only the terms the schema declares (the names the generator invented were the free path's retries).
TERMS = sorted(t.rsplit("#", 1)[1] for t in SC.TERMS if t.startswith(SC.NS))
GRAMMAR = (Path(__file__).parent / "resources" / "sparql.ebnf").read_text().replace(
    "SPHN_TERMS", " | ".join(f'"{t}"' for t in TERMS))
