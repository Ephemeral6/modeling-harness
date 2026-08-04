# Migrating Modeling Harness 4.1 projects to 4.2

4.2 is backward compatible for non-optimization projects.  It does not add a
workflow stage or require routine human approval.

For an existing optimization project:

1. Copy `config/optimization_assurance.json` from the 4.2 template and keep
   `mode: auto`, or set `mode: required` explicitly.
2. Merge the S1/S3/S5/S6 acceptance additions from
   `templates/config/problem_graph.seed.json` into the project Problem Graph.
3. Run `modelharness assurance init --project .` and complete the generated
   constraint ledger.
4. Add the independent candidate checker, feasibility audit and honest
   optimality scope described in `docs/optimization_assurance.md`.
5. Generate result provenance and the paper coverage matrix.  A human packet is
   needed only when `assurance status` returns `HUMAN_REQUIRED`.

Existing Evidence Graph, Workflow, tool runs, milestone stamps and reviews are
not replaced.  Do not delete historical review files during migration.
