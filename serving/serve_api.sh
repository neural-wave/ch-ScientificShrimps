#!/usr/bin/env bash
# The REST API as the jury calls it (POST :8080/nl-to-sparql), with schema linking on. The code index
# nl2q_data/codes.json holds labels of licensed terminologies (SNOMED CT, LOINC, ICD-10-GM, CHOP, ORDO), so it is
# never committed: it is built here from the ontology files on first start (~3 min), then reused.
set -euo pipefail
cd "$(dirname "$0")/.."
export NL2Q_LINK=1
ONTOLOGIES=${NL2Q_ONTOLOGIES:-/data/scratch/nw-data/virtuoso-release/ontologies}
if [ ! -s "${NL2Q_CODES:-nl2q_data/codes.json}" ] && [ -d "$ONTOLOGIES" ]; then
  python -m nl2q.tools.codes --dir "$ONTOLOGIES" --out "${NL2Q_CODES:-nl2q_data/codes.json}"
fi
exec python -m nl2q.api
