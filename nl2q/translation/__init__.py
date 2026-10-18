"""Translation layer: a researcher's request -> one SPARQL query for Virtuoso, translated literally (it never refuses
or repairs a request; the compliance layer judges it). The rest of nl2q calls only these two functions (pipeline.py,
api.py), so generate's signature is the contract with the compliance layer:
- generate(text, check, llm_fn, usage): (query, attempts); check is compliance.validate;
- warm_up(): indexes and generator ready before the first request.
Inside: prompt.py (system rules, schema card, few-shot examples, finish), examples.py (reads resources/examples.md),
resources/conventions.md (dataset wording for the prompt), llm.py (vLLM client),
generate.py (retry loop), codeclass.py (NL2Q_CODE_CLASS: a code node grouped, returned or compared becomes its class,
before the query is checked and judged)."""
from .generate import generate, warm_up
