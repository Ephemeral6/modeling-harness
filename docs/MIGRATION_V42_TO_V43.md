# Migrating Modeling Harness 4.2 projects to 4.3

4.3 is backward compatible for profiles that do not enable a Paper Content
Contract.  Existing numerical evidence, optimization assurance artifacts and
the S0–S6 stage model remain authoritative.

For an existing competition project:

1. Copy `config/paper_content_contract.json` and
   `paper/technical_appendix.md` from the 4.3 template.
2. Add
   `"paper_content_contract": "config/paper_content_contract.json"` to the
   active `cumcm` or `mcm_icm` delivery profile.
3. Add the conditional `paper_content_contract` acceptance item from the 4.3
   Problem Graph seed to the existing S6 node.
4. Add problem-specific `paper_obligations` to mandatory requirements that
   need full schedules, state diagrams, pseudocode, convergence tables or
   other executable detail.
5. Run `modelharness paper contract-init --project .`, fill
   `paper/content_coverage.json`, and move machine numeric lock tables from the
   body to `paper/technical_appendix.md`.
6. Run `modelharness paper content-audit --project .`.  Treat every returned
   issue as a hard content gap; treat the 18–25 page message as advisory.
7. Rebuild the PDF and run `modelharness paper audit --project .` before S6.

The old `paper/coverage_matrix.json` remains supported.  It answers whether a
requirement has named explanatory parts; the new matrix additionally proves
that those parts have the required representation, evidence, placement,
disclosures and detailed artifacts.  Do not delete either artifact in a
migrated optimization project.

