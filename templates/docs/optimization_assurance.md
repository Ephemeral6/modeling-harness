# Optimization assurance contract

Use this contract only when `modelharness assurance status --project .` reports
`relevant: true`.  The files below refine existing S1/S3/S5/S6 obligations; they
do not create another workflow.

## S1: constraint ledger

Run `modelharness assurance init --project .`, then classify every item in
`problem/constraint_ledger.json` as `MODELED`, `UNMODELED`, or
`NOT_APPLICABLE`.  A modeled hard constraint needs a mathematical form and two
implementation paths:

```json
{
  "status": "MODELED",
  "hard": true,
  "mathematical_form": "max_t occupied[t] <= 112",
  "implementation": {
    "solver_artifact": "src/solver.py",
    "checker_artifact": "src/feasibility.py"
  }
}
```

The checker may be small, but it must not reuse the solver's constraint-building
implementation.

## S3: feasibility and optimality

Write the selected candidate to `results/candidate_solution.json`.  The
independent checker writes `results/feasibility_audit.json`:

```json
{
  "schema": 1,
  "execution_status": "completed",
  "verdict": "pass",
  "authority": "machine",
  "constraint_ledger_sha256": "...",
  "candidate": {
    "id": "candidate-427",
    "artifact": "results/candidate_solution.json",
    "sha256": "...",
    "producer_id": "solver-agent"
  },
  "solver": {
    "artifact": "src/solver.py",
    "sha256": "...",
    "identity": "solver-agent"
  },
  "checker": {
    "artifact": "src/feasibility.py",
    "sha256": "...",
    "identity": "feasibility-checker",
    "implementation_reuse": false
  },
  "constraints": {
    "constraint.Q.capacity": {
      "execution_status": "completed",
      "verdict": "pass",
      "max_violation": 0.0,
      "tolerance": 1e-9
    }
  }
}
```

Then write `results/optimality.json`.  Never strengthen the scope merely for a
better narrative:

```json
{
  "schema": 1,
  "candidate_id": "candidate-427",
  "scope": "BOUNDED_GAP",
  "model_scope": "All schedules under the documented pen-sharing interpretation",
  "objective": {"sense": "max", "value": 427, "baseline_value": 416},
  "lower_bound": 427,
  "upper_bound": 434,
  "gap_fraction": 0.016129,
  "gap_definition": "bound",
  "feasibility_audit_sha256": "..."
}
```

Supported scopes are `GLOBAL_CERTIFICATE`, `RESTRICTED_CLASS_OPTIMAL`,
`BOUNDED_GAP`, `BEST_KNOWN_FEASIBLE`, `FEASIBLE_ONLY`, and `UNKNOWN`.
`gap_definition` may be `incumbent` or `bound`.

## S5: headline semantics and risk review

Every headline entry in `config/claim_bindings.json` declares `headline: true`
and one semantic type: `measurement`, `estimate`, `lower_bound`, `upper_bound`,
`feasible_solution`, `best_known_solution`, `optimal_solution`,
`simulation_mean`, or `recommendation`.  Candidate claims also bind
`candidate_id` and `optimality_scope`.

Run:

```powershell
modelharness assurance provenance --project .
modelharness assurance status --project .
```

Continue automatically for `AUTO_CONTINUE` or `HUMAN_RECOMMENDED`.  For
`HUMAN_REQUIRED`, generate the packet and let a domain reviewer write
`reviews/optimization_human_review.json` with schema 1, `authority: human`, an
independent `reviewer_id`, `verdict: approve`, and the current packet SHA-256.
A review cannot override a machine blocker.

## S6: explanation coverage

Run `modelharness assurance coverage-init --project .` and map each required
element to an actual heading, exact text anchor, and—where requested—verified
evidence ID in `paper/coverage_matrix.json`.  This is what catches a paper that
states “427” but omits its model, hard constraints, algorithm, derivation,
feasibility check, or scope.
