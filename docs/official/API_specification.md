# Natural Language to SPARQL API

Translates a natural language question into a SPARQL query over SPHN data stored in Virtuoso.

- `200` means the translation finished. A non-compliant query is still a successful response; `compliance_reason` explains why it is not compliant.
- `is_compliant` is true if the query is valid for Virtuoso (Virtuoso extensions such as `DEFINE` and `OPTION(inference)` are allowed) and is read-only (`SELECT`, `ASK`, `CONSTRUCT` or `DESCRIBE`).

## OpenAPI spec

```yaml
openapi: 3.0.3
info:
  title: Natural Language to SPARQL API
  description: Translates a natural language question into a SPARQL query over SPHN data stored in Virtuoso.
  version: 1.0.0
paths:
  /nl-to-sparql:
    post:
      summary: Translate natural language to SPARQL
      operationId: generateSparql
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [text]
              properties:
                text:
                  type: string
                  minLength: 1
                  example: "How many patients have a Billed Diagnosis for Type 2 diabetes mellitus (non-insulin-dependent diabetes mellitus)?"
      responses:
        '200':
          description: Translation finished. A non-compliant query is still a successful response.
          content:
            application/json:
              schema:
                type: object
                required: [sparql_query, tokens_consumed, is_compliant]
                properties:
                  sparql_query:
                    type: string
                  tokens_consumed:
                    type: integer
                    description: Total tokens used by the model for this request.
                  is_compliant:
                    type: boolean
                    description: True if the query is valid for Virtuoso (Virtuoso extensions such as DEFINE and OPTION(inference) are allowed) and is read-only (SELECT, ASK, CONSTRUCT or DESCRIBE).
                  compliance_reason:
                    type: string
                    description: Natural language explanation of why the query is not compliant. Present only when is_compliant is false.
              examples:
                compliant:
                  summary: Compliant query
                  value:
                    sparql_query: |
                      DEFINE sql:select-option "order"
                      PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
                      PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
                      PREFIX ucum: <https://biomedit.ch/rdf/sphn-resource/ucum/>
                      PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
                      PREFIX einstein: <https://biomedit.ch/rdf/sphn-resource/einstein/shortcuts#>

                      SELECT (COUNT(?pseudo_id) AS ?countResult)
                      WHERE {
                          {
                              SELECT DISTINCT(?g AS ?pseudo_id)
                              WHERE {
                                  GRAPH ?g {
                                      ?diagnosis0_code rdf:type <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/E11>
                                          OPTION(inference "https://biomedit.ch/rdf/sphn-resource/global") .
                                      ?diagnosis sphn:hasCode ?diagnosis0_code .
                                      ?diagnosis rdf:type sphn:BilledDiagnosis .
                                  }
                              }
                          }
                      }
                    tokens_consumed: 1287
                    is_compliant: true
                non_compliant:
                  summary: Non-compliant query
                  value:
                    sparql_query: |
                      PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
                      DELETE WHERE { GRAPH ?g { ?diagnosis a sphn:BilledDiagnosis . } }
                    tokens_consumed: 412
                    is_compliant: false
                    compliance_reason: "The query modifies data (DELETE); only read-only queries are allowed."
        default:
          description: Error (bad input or translation failure).
          content:
            application/json:
              schema:
                type: object
                required: [error]
                properties:
                  error:
                    type: string
                    example: "'text' must not be empty."
```
