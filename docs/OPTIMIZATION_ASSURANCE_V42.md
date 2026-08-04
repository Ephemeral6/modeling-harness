# Optimization Assurance 4.2

Optimization tasks need a symmetric quality loop: prevent invalid claims **and**
search for missed value.  Harness 4.2 adds that loop without adding fixed roles or
mandatory human checkpoints.

## Design boundary

- The agent still chooses the model, solver, search space, experiment design and
  stopping rule.
- The Harness does not prescribe a solver and does not require two full solver
  implementations.
- For hard optimization tasks, the submitted candidate is checked by a small,
  independent feasibility implementation.
- Human review is risk-triggered.  Ordinary tasks continue automatically.
- A human may resolve interpretation and scope, but cannot turn a mechanical FAIL
  into PASS.

## Existing-stage integration

| Stage | Added obligation when optimization is relevant |
|---|---|
| S1 | Map every source constraint into `problem/constraint_ledger.json`. |
| S3 | Produce a candidate, independent feasibility audit and explicit optimality scope. |
| S5 | Label headline numbers by semantic type and resolve only triggered human risks. |
| S6 | Close requirement-to-paper explanation coverage, including derivation and verification. |

The accepted optimality scopes are `GLOBAL_CERTIFICATE`,
`RESTRICTED_CLASS_OPTIMAL`, `BOUNDED_GAP`, `BEST_KNOWN_FEASIBLE`,
`FEASIBLE_ONLY`, and `UNKNOWN`.  A result such as “434 is an upper bound” can no
longer be conflated with “427 is a feasible solution” or “416 is optimal”.

## Minimal operating loop

1. Run `model-harness assurance init` and complete the generated constraint
   ledger during model specification.
2. Let the chosen solver write `results/candidate_solution.json`; write an
   independent checker and its `results/feasibility_audit.json` report.
3. Declare only the proven scope in `results/optimality.json`.
4. Mark headline claim bindings with `headline: true` and a `semantic_type`, then
   run `model-harness assurance provenance`.
5. Run `model-harness assurance status`.  If it says `HUMAN_REQUIRED`, generate
   the compact review packet; otherwise continue autonomously.
6. Fill the generated paper coverage matrix and let S6 verify the actual anchors
   and evidence.

These files are compact contracts and derived views, not a second source of truth.
