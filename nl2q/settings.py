"""Knobs for nl2q. Environment variables override the defaults.
One block per layer (README, Working together): add a knob to your own block, so two branches never edit the same lines."""
import os
from pathlib import Path

# shared
HERE = Path(__file__).parent
DATA = Path(os.getenv("NL2Q_DATA", "nl2q_data"))  # local and gitignored: code index, eval traces
SCHEMA = Path(os.getenv("NL2Q_SCHEMA", HERE / "config" / "schema.json"))  # python -m nl2q.tools.adapt
SPARQL_ENDPOINT = os.getenv("SPARQL_ENDPOINT", "http://localhost:8890/sparql")  # the hackathon's Virtuoso
INFERENCE = "https://biomedit.ch/rdf/sphn-resource/global"  # Virtuoso rule set (and graph) of schema + terminologies
GRAPH_PREFIX = "http://example.org/graph/"  # one named graph per patient file
API_HOST = os.getenv("NL2Q_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("NL2Q_API_PORT", 8080))

# translation layer
CODES = Path(os.getenv("NL2Q_CODES", DATA / "codes.json"))  # terminology labels: restricted licences, never commit
SELECT_ORDER = os.getenv("NL2Q_SELECT_ORDER", "1") == "1"  # prepend DEFINE sql:select-option "order"
MAX_RETRIES = 2          # regenerate when the query does not parse or uses terms the schema does not have
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.8-27b")  # any OpenAI-compatible server (vLLM on GPU 0)
LLM_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:8001/v1")  # serving/serve_llm.sh
THINK = os.getenv("NL2Q_THINK", "0") == "1"  # reasoning before the query: slower and more tokens
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")  # when THINK is on (low|medium|xhigh)
THINK_TOKENS = 8192      # cap on reasoning + query
MAX_TOKENS = 1536        # cap on a query without reasoning
LLM_TIMEOUT = 180        # seconds per request
SEED = int(os.getenv("NL2Q_SEED", 0))  # reproducible sampling
EXAMPLES = Path(os.getenv("NL2Q_EXAMPLES", HERE / "config" / "examples.md"))  # python -m nl2q.tools.make_examples
SHOTS = int(os.getenv("NL2Q_SHOTS", 4))  # examples most similar to the request, added to the `always` ones
HOLDOUT = os.getenv("NL2Q_HOLDOUT", "0") == "1"  # evaluation: never show the request's own example (or its family)
GRAMMAR = os.getenv("NL2Q_GRAMMAR", "0") == "1"  # free generation can only write SPARQL that parses (translation/grammar.py)
PLAN = os.getenv("NL2Q_PLAN", "0") == "1"  # write a JSON plan first and compile it (translation/plan.py); free SPARQL otherwise
PLAN_URL = os.getenv("NL2Q_PLAN_URL", LLM_URL)  # the plan writer: the generator itself, or a model fine-tuned on plans
PLAN_MODEL = os.getenv("NL2Q_PLAN_MODEL", LLM_MODEL)  # == LLM_MODEL: instructions + similar plan examples in the prompt
PLAN_SHOTS = int(os.getenv("NL2Q_PLAN_SHOTS", 8))  # plan examples shown to a model not fine-tuned on plans
PLAN_CANDIDATES = 1      # plans per request (query_check and clean_test print it in their run headers)
FIXED_CORE = os.getenv("NL2Q_FIXED_CORE", "0") == "1"  # core schema blocks in SYSTEM: a longer head served from vLLM's prefix cache
WARM_PREFIX = os.getenv("NL2Q_WARM_PREFIX", "0") == "1"  # warm_up() sends the fixed head once: request 1 reads it cached too
SHAPE_RULE = os.getenv("NL2Q_SHAPE_RULE", "1") == "1"  # static rule: OPTIONAL only for unmatched rows, a group flag is EXISTS
CARD_STATS = os.getenv("NL2Q_CARD_STATS", "0") == "1"  # each schema block says in how many patients its type occurs
LINK = os.getenv("NL2Q_LINK", "1") == "1"  # schema linking (on; 0: the data-filtered card of schema.json): schema-only card (config/card.json), retrieval over definitions,
# static code lookup, and validate()'s class/SQL/unbound-variable feedbacks
CODE_CLASS = os.getenv("NL2Q_CODE_CLASS", "1") == "1"  # static rewrite: a code node grouped, returned or compared -> its class
CODE_IRI = os.getenv("NL2Q_CODE_IRI", "0") == "1"  # a plan's breakdown by code returns the code's class IRI, as the
# organizers' queries do (off: its short name); ICD-10-GM and CHOP keep the short name (it merges their year versions)
DIALECT = os.getenv("NL2Q_DIALECT", "1") == "1"  # static rewrite: ROUND(x, n) and EXISTS left of && / || -> forms Virtuoso accepts
RULES2 = os.getenv("NL2Q_RULES2", "0") == "1"  # conventions-extra.md appended (off: O04 4,952 -> 0 in 2 of 2 runs)
CARD = Path(os.getenv("NL2Q_CARD", HERE / "config" / "card.json"))  # the linked card: python -m nl2q.tools.adapt --linked

# compliance layer
# Shapes of patient and record identifiers in the data (full scan of the 100,000 mock patients, 17 Oct): every
# sphn:hasIdentifier of a record is ID-<7-digit patient number><7-digit counter>, and resource IRIs end in a UUID that
# no two patients share. A constant with one of these shapes names a patient or a record, wherever the query puts it.
IDENTIFIER_SHAPES = (r"\bID-\d{6,}", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
PRESCREEN = os.getenv("NL2Q_PRESCREEN", "0") == "1"  # d3 judges the request before generation; it can only block
PRESCREEN_MIN_CONF = 0.9  # block when P(not proceed) reaches this: a false block cannot be undone, a miss costs nothing
PRESCREEN_LOG_ONLY = {"not_aggregated"}  # rules d3 only logs, never blocks on (R2 until the bench shows no false block)
DECISION_URL = os.getenv("DECISION_URL", "http://localhost:8002")  # serving/serve_decision.sh: vllm-sr/d3 on GPU 1
PRESCREEN_TIMEOUT = float(os.getenv("NL2Q_PRESCREEN_TIMEOUT", 1.0))  # seconds, then the request proceeds without d3:
# awake it answers in ~0.3 s; asleep it reloads in ~20 s, so the request that wakes it goes on to generation (IDLE_MINUTES=0 for the jury)
# dynamic check (organizer, 17 Oct): it runs the query on the database, so it is a stage of an evaluation that executes
# queries (e.g. after a timeout), not part of the static system; off for the evaluation
KANON = os.getenv("NL2Q_KANON", "off")  # k-anonymity on compliant queries (off|flag|rewrite), compliance/kanon.py:
# flag refuses a query whose result has a group of 1 to K-1 distinct patients (runs it once on SPARQL_ENDPOINT);
# rewrite adds HAVING (COUNT(DISTINCT ?patient) >= K) so Virtuoso drops those groups. Any other value is off
K = int(os.getenv("NL2Q_K", 10))  # smallest group of patients a result may release (organizers: results under 10 are dropped)
KANON_TIMEOUT = float(os.getenv("NL2Q_KANON_TIMEOUT", 30))  # seconds for the flag run, then the rules' verdict stands

# optimizer (nl2q/optimize): rewrites the generated query before it is judged and returned, same results, faster in
# Virtuoso; python -m nl2q.tools.optbench measures every pass at 100k
OPTIMIZE = os.getenv("NL2Q_OPTIMIZE", "0") == "1"
OPT_PASSES = tuple(p for p in os.getenv("NL2Q_OPT_PASSES", "paths,reorder,pragma").split(",") if p)  # in this order
OPT_SOURCE = os.getenv("NL2Q_OPT_STATS", "schema")  # schema: SHACL 2026.1 only (static); data: the 100k counts (dynamic)
OPT_SHACL = HERE / "optimize" / "shacl.json"  # SHACL 2026.1 cardinalities (optbench stats): what the static optimizer reads
OPT_STATS = HERE / "optimize" / "cardinality.json"  # counts of the 100k mock patients (optbench stats)
# dynamic, database-dependent, off for the evaluation: OPT_SOURCE=data, SLOWPATH, translation's CARD_STATS, compliance's KANON
SLOWPATH = os.getenv("NL2Q_SLOWPATH", "0") == "1"  # one more generator call for a compliant query predicted slow
SLOWPATH_T = float(os.getenv("NL2Q_SLOWPATH_T", 1.78e6))  # rows visited (optimize/cost.py), optbench calibrate
