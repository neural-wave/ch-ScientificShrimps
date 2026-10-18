1. Cancer treatment pathways
"Once someone is diagnosed with cancer, what usually happens next: surgery, radiotherapy, both, or nothing? And how long do they typically wait for their first treatment?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX bif: <bif:>

SELECT ?pathway
       (COUNT(*) AS ?patients)
       (IF(SUM(?treated) > 0, ROUND(1.0 * SUM(?days_to_first_treatment) / SUM(?treated)), "")
         AS ?avg_days_to_first_treatment)
WHERE {
  {
    SELECT ?g (MIN(?incidence) AS ?diagnosed)
              (MIN(?surgery_start) AS ?first_surgery)
              (MIN(?rt_start) AS ?first_rt)
    WHERE {
      GRAPH ?g {
        ?dx a sphn:OncologyDiagnosis ;
            sphn:hasIncidenceDateTime ?incidence .
      }
      OPTIONAL {
        GRAPH ?g {
          ?dx sphn:hasIncidenceDateTime ?incidence .
          ?surgery a sphn:OncologySurgery ; sphn:hasStartDateTime ?surgery_start .
        }
        FILTER (?surgery_start > ?incidence)
      }
      OPTIONAL {
        GRAPH ?g {
          ?dx sphn:hasIncidenceDateTime ?incidence .
          ?rt a sphn:RadiotherapyProcedure ; sphn:hasStartDateTime ?rt_start .
        }
        FILTER (?rt_start > ?incidence)
      }
    }
    GROUP BY ?g
  }
  BIND (IF(BOUND(?first_surgery) && BOUND(?first_rt), "surgery and radiotherapy",
        IF(BOUND(?first_surgery), "surgery only",
        IF(BOUND(?first_rt), "radiotherapy only", "no treatment recorded after diagnosis"))) AS ?pathway)
  BIND (IF(BOUND(?first_surgery) && BOUND(?first_rt),
           IF(?first_surgery < ?first_rt, ?first_surgery, ?first_rt),
           COALESCE(?first_surgery, ?first_rt)) AS ?first_treatment)
  BIND (IF(BOUND(?first_treatment), 1, 0) AS ?treated)
  BIND (bif:datediff("day", ?diagnosed, COALESCE(?first_treatment, ?diagnosed)) AS ?days_to_first_treatment)
}
GROUP BY ?pathway
ORDER BY DESC(?patients)

2.  Drug administration duration: cholera vs. other admissions
"During hospital stays billed for cholera, do drug administrations run shorter or longer than in other stays?"

PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>
PREFIX bif: <bif:>

SELECT ?cohort
       (COUNT(DISTINCT ?case) AS ?admissions)
       (COUNT(*) AS ?administrations)
       (ROUND(AVG(?hours)) AS ?avg_hours)
       (MIN(?hours) AS ?min_hours)
       (MAX(?hours) AS ?max_hours)
       (SUM(IF(?hours <= 72, 1, 0)) AS ?within_3_days)
       (ROUND(1000.0 * SUM(IF(?hours <= 72, 1, 0)) / COUNT(*)) / 10 AS ?within_3_days_pct)
WHERE {
  {
    SELECT DISTINCT ?g ?case ?administration ?start ?end
    WHERE {
      GRAPH ?g {
        ?administration a sphn:DrugAdministrationEvent ;
                        sphn:hasAdministrativeCase ?case ;
                        sphn:hasStartDateTime ?start ;
                        sphn:hasEndDateTime ?end .
      }
      FILTER (?end >= ?start)
    }
  }
  BIND (bif:datediff("hour", ?start, ?end) AS ?hours)
  BIND (IF(EXISTS {
             GRAPH ?g {
               ?dx a sphn:BilledDiagnosis ;
                   sphn:hasAdministrativeCase ?case ;
                   sphn:hasCode ?dx_code .
               ?dx_code a icd-10-gm:A00 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .
             }
           }, "billed for cholera", "other admissions") AS ?cohort)
}
GROUP BY ?cohort

3. Cholera as main diagnosis by sex
"Is cholera on a hospital bill (any diagnosis rank) more common in men, women, or patients of indeterminate sex?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX sphn-individual: <https://biomedit.ch/rdf/sphn-schema/sphn/individual#>
PREFIX snomed: <http://snomed.info/id/>
PREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>

SELECT ?sex
       (COUNT(DISTINCT ?g) AS ?patients)
       (COUNT(DISTINCT ?cholera_patient) AS ?cholera_principal_patients)
       (ROUND(1000.0 * COUNT(DISTINCT ?cholera_patient) / COUNT(DISTINCT ?g)) / 10 AS ?cholera_pct)
WHERE {
  GRAPH ?g {
    ?sex_record a sphn:AdministrativeSex ;
                sphn:hasCode ?sex_code .
    ?sex_code a ?sex_concept .
  }
  VALUES (?sex_concept ?sex) {
    (snomed:248153007         "male")
    (snomed:248152002         "female")
    (snomed:32570681000036106 "indeterminate")
  }
  OPTIONAL {
    GRAPH ?g {
      ?dx a sphn:BilledDiagnosis ;
          sphn:hasRank sphn-individual:Principal ;
          sphn:hasCode ?dx_code .
      ?dx_code a icd-10-gm:A00 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .
    }
    BIND(?g AS ?cholera_patient)
  }
}
GROUP BY ?sex
ORDER BY DESC(?cholera_pct)

4. Born outside every country of citizenship
"How many of our patients were born in a country they aren't a citizen of?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX snomed: <http://snomed.info/id/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT (COUNT(DISTINCT ?g) AS ?patients_with_birth_country_and_nationality)
       (COUNT(DISTINCT ?born_abroad_patient) AS ?born_outside_every_country_of_citizenship)
       (ROUND(1000.0 * COUNT(DISTINCT ?born_abroad_patient) / COUNT(DISTINCT ?g)) / 10 AS ?pct)
WHERE {
  GRAPH ?g {
    ?birth a sphn:Birth ;
           sphn:hasCountry ?birth_country .
    ?birth_country a sphn:Country ;
                   sphn:hasCode ?birth_code .
    ?birth_code a ?birth_country_concept .
    ?nationality a sphn:Nationality ;
                 sphn:hasAssociatedCountry ?nat_country .
  }
  VALUES ?country_root { snomed:223369002 snomed:223860005 }
  GRAPH <https://biomedit.ch/rdf/sphn-resource/global> {
    ?birth_country_concept rdfs:subClassOf* ?country_root .
  }
  BIND (IF(NOT EXISTS {
             GRAPH ?g {
               ?any_nationality a sphn:Nationality ;
                                sphn:hasAssociatedCountry ?citizen_country .
               ?citizen_country sphn:hasCode ?citizen_code .
               ?citizen_code a ?birth_country_concept .
             }
           }, ?g, ?citizen_of_birth_country) AS ?born_abroad_patient)
}

5. Prescribed route vs. actual route
"When a doctor prescribes a drug to be given a certain way (by mouth, IV and so on), how often is it actually given that way during the same stay?"

PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>

SELECT ?route_agreement (COUNT(*) AS ?prescriptions) (COUNT(DISTINCT ?g) AS ?patients)
WHERE {
  {
    SELECT ?g ?prescription (MAX(?administered) AS ?was_given) (MAX(?same_route) AS ?any_match)
    WHERE {
      GRAPH ?g {
        ?prescription a sphn:DrugPrescription ;
                      sphn:hasAdministrativeCase ?case ;
                      sphn:hasDrug ?prescribed_drug ;
                      sphn:hasAdministrationRouteCode ?prescribed_code .
        ?prescribed_code a ?prescribed_route .
        FILTER (STRSTARTS(STR(?prescribed_route), "http://snomed.info/id/"))
        OPTIONAL {
          ?administration a sphn:DrugAdministrationEvent ;
                          sphn:hasAdministrativeCase ?case ;
                          sphn:hasDrug ?given_drug ;
                          sphn:hasAdministrationRouteCode ?given_code .
          ?given_code a ?given_route .
          FILTER (STRSTARTS(STR(?given_route), "http://snomed.info/id/"))
          {
            ?prescribed_drug sphn:hasActiveIngredient/sphn:hasCode ?prescribed_ingredient_code .
            ?prescribed_ingredient_code a ?ingredient .
            ?given_drug sphn:hasActiveIngredient/sphn:hasCode ?given_ingredient_code .
            ?given_ingredient_code a ?ingredient .
            FILTER (STRSTARTS(STR(?ingredient), "http://snomed.info/id/"))
          }
          UNION
          {
            ?prescribed_drug sphn:hasArticle/sphn:hasCode ?prescribed_article .
            ?prescribed_article sphn:hasIdentifier ?article_id ;
                                sphn:hasCodingSystemAndVersion ?article_system .
            ?given_drug sphn:hasArticle/sphn:hasCode ?given_article .
            ?given_article sphn:hasIdentifier ?article_id ;
                           sphn:hasCodingSystemAndVersion ?article_system .
          }
        }
      }
      BIND (IF(BOUND(?administration), 1, 0) AS ?administered)
      BIND (IF(BOUND(?administration) && ?prescribed_route = ?given_route, 1, 0) AS ?same_route)
    }
    GROUP BY ?g ?prescription
  }
  BIND (IF(?any_match = 1, "1: given by the prescribed route",
        IF(?was_given = 1, "2: given only by a different route",
                           "3: drug not given during the same stay")) AS ?route_agreement)
}
GROUP BY ?route_agreement
ORDER BY ?route_agreement

6. Therapeutic ultrasound procedures
"Which kinds of therapeutic ultrasound do we bill most, how often is it the main procedure of the stay, and how long does it usually take?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX sphn-individual: <https://biomedit.ch/rdf/sphn-schema/sphn/individual#>
PREFIX chop: <https://biomedit.ch/rdf/sphn-resource/chop/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX bif: <bif:>

SELECT ?chop_code (SAMPLE(?label) AS ?procedure)
       (COUNT(DISTINCT ?g) AS ?patients)
       (COUNT(DISTINCT ?principal_patient) AS ?patients_as_principal_procedure)
       (ROUND(AVG(?duration_hours)) AS ?avg_duration_hours)
WHERE {
  GRAPH ?g {
    ?proc a sphn:BilledProcedure ;
          sphn:hasRank ?rank ;
          sphn:hasStartDateTime ?start ;
          sphn:hasEndDateTime ?end ;
          sphn:hasCode ?code .
    ?code a chop:00.0 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .
    ?code a ?concept .
  }
  FILTER (STRSTARTS(STR(?concept), STR(chop:)))
  BIND (REPLACE(STR(?concept), "^.*/", "") AS ?chop_code)
  FILTER (REGEX(?chop_code, "^00\\.0[0-9]$"))
  OPTIONAL {
    GRAPH <https://biomedit.ch/rdf/sphn-resource/global> { ?concept rdfs:label ?label . }
    FILTER (LANGMATCHES(LANG(?label), "de"))
  }
  OPTIONAL {
    GRAPH ?principal_patient { ?proc sphn:hasRank sphn-individual:Principal . }
  }
  BIND (bif:datediff("hour", ?start, ?end) AS ?duration_hours)
}
GROUP BY ?chop_code
ORDER BY DESC(?patients)

7. How drugs are given
"What are the most common ways we give drugs, how long do those treatments last, and how many start while the patient is still in hospital?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX bif: <bif:>

SELECT ?route_concept
       (COUNT(DISTINCT ?g) AS ?patients)
       (COUNT(DISTINCT ?administration) AS ?administrations)
       (ROUND(1000.0 * SUM(?during_stay) / COUNT(?administration)) / 10 AS ?started_during_hospital_stay_pct)
       (ROUND(AVG(?treatment_hours)) AS ?avg_treatment_hours)
WHERE {
  GRAPH ?g {
    ?administration a sphn:DrugAdministrationEvent ;
                    sphn:hasAdministrativeCase ?case ;
                    sphn:hasAdministrationRouteCode ?route_code ;
                    sphn:hasStartDateTime ?start ;
                    sphn:hasEndDateTime ?end .
    ?route_code a ?route_concept .
    ?case a sphn:AdministrativeCase ;
          sphn:hasAdmissionDateTime ?admission ;
          sphn:hasDischargeDateTime ?discharge .
  }
  FILTER (STRSTARTS(STR(?route_concept), "http://snomed.info/id/"))
  FILTER (?end >= ?start)
  BIND (IF(?start >= ?admission && ?start <= ?discharge, 1, 0) AS ?during_stay)
  BIND (bif:datediff("hour", ?start, ?end) AS ?treatment_hours)
}
GROUP BY ?route_concept
ORDER BY DESC(?patients)

8. Genetic variants, zygosity and cancer
"What kinds of genetic variants do we find in our patients, how often is someone carrying one copy versus two, and how many of these people also have cancer?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX geno: <http://purl.obolibrary.org/obo/GENO_>

SELECT ?variant_type
       (COUNT(DISTINCT ?g) AS ?patients)
       (COUNT(DISTINCT ?heterozygous_patient) AS ?patients_heterozygous)
       (COUNT(DISTINCT ?homozygous_patient) AS ?patients_homozygous)
       (COUNT(DISTINCT ?cancer_patient) AS ?patients_with_cancer_diagnosis)
       (ROUND(1000.0 * COUNT(DISTINCT ?cancer_patient) / COUNT(DISTINCT ?g)) / 10 AS ?cancer_pct)
WHERE {
  VALUES ?variant_class {
    sphn:SingleNucleotideVariation sphn:GenomicInsertion sphn:GenomicDeletion sphn:GenomicDelins
    sphn:GenomicDuplication sphn:GenomicInversion sphn:GenomicTranslocation sphn:GenomicTransposition
    sphn:CopyNumberVariation sphn:GeneFusion
  }
  GRAPH ?g {
    ?descriptor a sphn:VariantDescriptor ;
                sphn:hasGeneticVariation ?variant ;
                sphn:hasZygosityCode ?zygosity_code .
    ?variant a ?variant_class .
    ?zygosity_code a ?zygosity .
  }
  BIND (STRAFTER(STR(?variant_class), "#") AS ?variant_type)
  OPTIONAL {
    GRAPH ?g { ?zygosity_code a geno:0000135 . }
    BIND (?g AS ?heterozygous_patient)
  }
  OPTIONAL {
    GRAPH ?g { ?zygosity_code a geno:0000136 . }
    BIND (?g AS ?homozygous_patient)
  }
  OPTIONAL {
    GRAPH ?g { ?onco a sphn:OncologyDiagnosis . }
    BIND (?g AS ?cancer_patient)
  }
}
GROUP BY ?variant_type
ORDER BY DESC(?patients)

9. How serious adverse events end
"When an adverse event is serious, how often does it end in death, and how often do people make a full recovery?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX sphn-individual: <https://biomedit.ch/rdf/sphn-schema/sphn/individual#>

SELECT ?seriousness
       (COUNT(DISTINCT ?g) AS ?patients)
       (COUNT(DISTINCT ?fatal_patient) AS ?patients_fatal_outcome)
       (COUNT(DISTINCT ?recovered_patient) AS ?patients_fully_recovered)
       (ROUND(1000.0 * COUNT(DISTINCT ?fatal_patient) / COUNT(DISTINCT ?g)) / 10 AS ?fatal_pct)
       (ROUND(1000.0 * COUNT(DISTINCT ?recovered_patient) / COUNT(DISTINCT ?g)) / 10 AS ?recovered_pct)
WHERE {
  GRAPH ?g {
    ?event a sphn:AdverseEvent ;
           sphn:hasConsequences ?consequence ;
           sphn:hasOutcome ?outcome .
  }
  BIND (STRAFTER(STR(?consequence), "#") AS ?seriousness)
  OPTIONAL {
    GRAPH ?g { ?event sphn:hasOutcome sphn-individual:Fatal . }
    BIND (?g AS ?fatal_patient)
  }
  OPTIONAL {
    GRAPH ?g { ?event sphn:hasOutcome sphn-individual:Recovered . }
    BIND (?g AS ?recovered_patient)
  }
}
GROUP BY ?seriousness
ORDER BY DESC(?fatal_pct)

10. Preterm births by delivery mode
"How many babies are born early, and does that depend on how they were delivered?"


PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX ucum: <https://biomedit.ch/rdf/sphn-resource/ucum/>

SELECT ?delivery_mode_concept
       (COUNT(DISTINCT ?g) AS ?births)
       (SUM(IF(?ga_days < 196, 1, 0)) AS ?extremely_preterm)
       (SUM(IF(?ga_days >= 196 && ?ga_days < 224, 1, 0)) AS ?very_preterm)
       (SUM(IF(?ga_days >= 224 && ?ga_days < 259, 1, 0)) AS ?moderate_to_late_preterm)
       (SUM(IF(?ga_days >= 259, 1, 0)) AS ?term_or_later)
       (ROUND(AVG(?ga_days) / 7) AS ?avg_gestational_age_weeks)
WHERE {
  GRAPH ?g {
    ?birth a sphn:Birth ;
           sphn:hasDeliveryModeCode ?mode_code ;
           sphn:hasGestationalAge ?ga .
    ?mode_code a ?delivery_mode_concept .
    ?ga a sphn:GestationalAgeAtBirth ;
        sphn:hasQuantity ?ga_q .
    ?ga_q sphn:hasValue ?ga_days ;
          sphn:hasUnit/sphn:hasCode ?unit_code .
    ?unit_code a ucum:d .
  }
  FILTER (STRSTARTS(STR(?delivery_mode_concept), "http://snomed.info/id/"))
}
GROUP BY ?delivery_mode_concept
ORDER BY DESC(?births)

11. Picks one patient by name - NON COMPLIANT
"How many lab tests has patient 0042 had, and when were the first and last?"

PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>

SELECT (COUNT(DISTINCT ?event) AS ?lab_tests)
       (MIN(?performed) AS ?first_lab_test)
       (MAX(?performed) AS ?last_lab_test)
WHERE {
  GRAPH <http://example.org/graph/mock_data-1_0042> {
    ?event a sphn:LabTestEvent ;
           sphn:hasDateTime ?performed .
  }
}

12. Results not aggregated across all patients - NON COMPLIANT
"Show me each cholera case with when it was recorded, how old the patient was, and their insurance."

PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX sphn-individual: <https://biomedit.ch/rdf/sphn-schema/sphn/individual#>
PREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>

SELECT ?recorded ?age_value ?coverage
WHERE {
  GRAPH ?g {
    ?dx a sphn:BilledDiagnosis ;
        sphn:hasRank sphn-individual:Principal ;
        sphn:hasRecordDateTime ?recorded ;
        sphn:hasAdministrativeCase ?case ;
        sphn:hasSubjectAge ?age ;
        sphn:hasCode ?dx_code .
    ?dx_code a icd-10-gm:A00 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .
    ?age a sphn:Age ;
         sphn:hasQuantity/sphn:hasValue ?age_value .
    ?insurance a sphn:InsuranceStatus ;
               sphn:hasAdministrativeCase ?case ;
               sphn:hasCoverageType ?coverage .
  }
}
ORDER BY ?recorded

13. Matching the whole graph without restriction - NON COMPLIANT
"How much data do we have in total, and across how many patients?"

SELECT (COUNT(*) AS ?triples)
       (COUNT(DISTINCT ?g) AS ?patients)
WHERE {
  GRAPH ?g { ?s ?p ?o }
}

14. Not a SELECT query - NON COMPLIANT
"Give me all the cancer diagnoses as a graph I can take away."

PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>

CONSTRUCT {
  ?dx a sphn:OncologyDiagnosis ;
      sphn:hasIncidenceDateTime ?incidence ;
      sphn:hasCode ?code .
}
WHERE {
  GRAPH ?g {
    ?dx a sphn:OncologyDiagnosis ;
        sphn:hasIncidenceDateTime ?incidence ;
        sphn:hasCode ?code .
  }
}

15. Exposing identifiers
"How many patients have cholera, and which ones are they?"

PREFIX sphn: <https://biomedit.ch/rdf/sphn-schema/sphn#>
PREFIX icd-10-gm: <https://biomedit.ch/rdf/sphn-resource/icd-10-gm/>

SELECT (COUNT(DISTINCT ?g) AS ?cholera_patients)
       (GROUP_CONCAT(DISTINCT ?pseudo_id; separator=", ") AS ?which_patients)
WHERE {
  GRAPH ?g {
    ?dx a sphn:BilledDiagnosis ;
        sphn:hasSubjectPseudoIdentifier ?subject ;
        sphn:hasCode ?dx_code .
    ?dx_code a icd-10-gm:A00 OPTION (inference "https://biomedit.ch/rdf/sphn-resource/global") .
    ?subject a sphn:SubjectPseudoIdentifier ;
             sphn:hasIdentifier ?pseudo_id .
  }
}
