# Rental Housing Law Navigator (Hack-Nation x RealPage, Challenge 2)

**Not legal advice.** This is a hackathon prototype. Every answer cites its source text, retrieval date and as-of date.

Given any sample apartment address, the system answers: which housing rules apply here on the query date, and how do the five supplied change cases (T1 to T5) affect the answer?

- **Live demo:** [add Hugging Face Space link]
- **Team:** [team name and members]
- **Videos:** [team intro] · [demo] · [technical walkthrough]

## Results at a glance (as of 2026-10-01)

| What | Result |
|---|---|
| Corpus documents processed | 87 |
| Rule records extracted (raw, then final) | 364 raw, 52 final |
| Schema valid / verbatim quoted spans | 52/52 · 52/52 |
| Sample addresses with lookups | 500/500 |
| Addresses resolved to a legal city by the Census Geocoder | 484 (16 no match, reported as unknown at city level) |
| Lookup results | applies 2,770 · unknown 1,395 · pending 362 · superseded 143 · not_yet_effective 140 |

### Change tests (affected addresses, conflict flags)

| Test | Affected | Conflict flags | What it shows |
|---|---|---|---|
| T1 CA AB 325 / SB 763 | 250 | 0 | not_yet_effective on 2025-12-31, applies on 2026-01-02 for every CA address |
| T2 Hoboken and Jersey City bans | 89 | 0 | Jersey City 50 + Hoboken 39, Newark 0 |
| T3 NJ FAIR Act | 140 | 89 | not_yet_effective on 2026-10-01, applies 2027-07-02, conflict flagged only in Jersey City and Hoboken |
| T4 MA S.2983 and H.5222 | 110 | 0 | Reported as pending, never in force; all 110 MA addresses would be affected if enacted |
| T5 MA rent-control ballot question | 0 | 0 | Struck 2026-06-23, recorded as failed; no rent cap reported in Boston or Cambridge |

The self-check cell (cell 6) prints PASS on all 11 checks. score.py and the dev key were not released to participants for this edition, so this is our validation.

## How to run

1. Open `housing_law_navigator.ipynb` in Google Colab.
2. Add your Anthropic key as a Colab secret named `ANTHROPIC_API_KEY`.
3. Put the participant pack (`MIT-hackathon-PARTICIPANT-PACK-CLEAN-NO-HOUR16.zip`, unzipped) in Google Drive and set the pack path in the setup cell.
4. Run the cells in order: setup, Module A extraction (cached per document in `extract_cache/`, so reruns are free), cell 3 (rule build, re-anchoring and collapse), cell 4 (rule-based duplicate drop, geocoding cached in `jurisdictions.json`, coverage and lookups), cell 5 (change tests), cell 6 (self-check), cell 7 (export), cell 8 (demo app).
5. Outputs are written to the output folder.

## Output files

| File | Contents |
|---|---|
| `rules.json` | 52 rule records in the supplied schema, each with citation and verbatim quoted span |
| `lookups.json` | Results for all 500 addresses as of 2026-10-01 |
| `changes.json` | Affected addresses and conflict flags for T1 to T5 |
| `rules_full.json` | Rules plus source doc, source URL, retrieval date, span verification and source type, for the UI and audit |
| `audit_log.json` | Model, generation time, geocoder, as-of date, dropped duplicates, re-anchored rules, and per-document retrieval date, URL, token usage and skip reason |
| `jurisdictions.json` | Census Geocoder result for every address |

## Pipeline

**Module A, extraction.** Each corpus document is sent to Claude (`claude-sonnet-5-5`) with the rule schema and asked for one record per rule. Every quoted span is matched back against the source text and snapped to the verbatim sentence (similarity 0.80 or higher, otherwise marked unverified). Rules are then deduplicated by subsection, filtered to the 13 in-scope jurisdictions, and collapsed to one rule per jurisdiction, category and status. Pending and failed items keep their own slots so T4 and T5 can see them. When two records compete for a slot, supplied corpus text beats self-saved pages, then verified spans, then official sources, then model confidence.

**Module B, address lookup.** Each address goes through the U.S. Census Geocoder (Public_AR_Current, Incorporated Places layer). The postal city is never trusted: Dorchester, Roxbury, Allston, Brighton, East Boston and other neighborhoods resolve to Boston, San Ysidro resolves to San Diego, and one "Cambridge" mailing address is legally in Boston. Coverage tests check unit count, year built, certificate-of-occupancy cutoffs and owner type. Any test that depends on a fact the data lacks returns unknown with the reason. Stricter local rent and just-cause ordinances mark the state rule superseded.

**Module C, change tracking.** The supplied T1 to T5 definitions are mapped to rule ids, and the lookup engine is rerun at each test's as-of dates.

## Responsible design

- Every interface says "not legal advice" and shows the as-of date.
- Enacted, not-yet-effective, pending and failed law are kept separate.
- Unknown is reported with a plain reason (owner data excluded 788, year built missing 245, legal city unconfirmed 193, unit count missing 110, local coverage unconfirmed 58).
- Conflicts between state and local algorithmic-pricing rules are flagged for human review.
- 15 rules started from link-only pages we saved one at a time with a retrieval date (allowed by organizers). Before picking each slot's winner, the pipeline looks for the same sentence in the supplied corpus. 3 were re-anchored there; the other 12 stay tagged `self_saved_link_only` with confidence capped at 0.5.
- Duplicates are dropped by a rule, not by hand: a city-level pending proposal is dropped when that city already has an in-force law in the same category, and a repeat pending or failed record with the same citation is dropped. On this run that removed r-0048, r-0049 (proposed versions of San Diego Mun. Code §§ 98.1101 to 98.1104, already in force as r-0003), r-0056 (a news write-up of the Santa Ana ordinance in r-0004) and r-0016 (a second copy of the failed Boston bill H.3744). The dropped ids are written to `audit_log.json`.

## Known open questions in the law

These are listed for human review. The pipeline stores one effective date per rule, so a reviewer should confirm the date in each case.

- Berkeley ch. 13.63 algorithmic ban: March 1, 2026 in the ordinance text vs January 2026 in an August 2026 law-firm alert.
- Los Angeles RSO formula: 2026-02-02 per LAHD vs 2026-01-24 per a landlord association.
- NJ FAIR Act may preempt the Jersey City and Hoboken ordinances once effective. The system flags this conflict on 89 addresses in T3.
- California's screening-fee cap has no single official 2026 dollar figure.

## Scaling to new jurisdictions

Add the new documents to the corpus and the addresses to the sample. Extraction, the geocoder (national) and the coverage engine run unchanged. Only the in-scope jurisdiction list and any new coverage fields need editing.

*Not legal advice. Summaries of law here are for a prototype.*
