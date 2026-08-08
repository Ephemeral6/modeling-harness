# Paper Content Contract 4.3

Modeling Harness 4.3 adds an exposition-completeness Gate for competition
profiles.  Its purpose is to prevent a paper from passing merely because its
headline numbers are correct and every existing sentence has evidence.

## Two independent directions

The existing evidence path remains unchanged:

```text
paper claim -> verified evidence -> machine artifact
```

The new path runs in the opposite direction:

```text
mandatory requirement
  -> required exposition obligations
  -> body or technical-appendix section
  -> representation + verified evidence + detailed artifact
  -> results/paper_coverage.json
  -> S6 Paper Content Gate
```

Both paths must pass.  A correct number does not excuse a missing derivation;
a long derivation does not excuse an unverified number.

## Project files

| File | Role |
|---|---|
| `config/paper_content_contract.json` | Profile-level exposition defaults, placement policy and advisory page target |
| `problem/requirements.json` | Optional, problem-specific `paper_obligations` |
| `paper/content_coverage.json` | Writer-completed requirement-to-section worksheet |
| `paper/final.md` | Authoritative competition-paper body |
| `paper/technical_appendix.md` | Detailed executable schedules, audit tables and machine-oriented material |
| `results/paper_coverage.json` | Derived mechanical audit report; never a second source of truth |

The contract is opt-in through `delivery_profile.paper_content_contract`.
`cumcm` and `mcm_icm` enable it; `general` and `real_world` retain their
existing behavior.

## Default answer obligations

Every mandatory answer in a competition profile must contain:

1. `direct_answer`: answer the subquestion directly and state scope;
2. `model_definition`: variables, parameters, objective/state and constraints;
3. `derivation`: the mathematical bridge from assumptions to computation;
4. `algorithm`: reviewable pseudocode, not only a solver name;
5. `validation`: method, expected result, actual result and conclusion;
6. `interpretation`: operational or managerial meaning and limitations.

The Gate checks the declared target and section, nonempty anchors, required
representation (equation/table/pseudocode/figure/flowchart), verified and fresh
evidence IDs, validation disclosure fields and artifact hashes.

## Problem-specific obligations

Requirements can add load-bearing detail without hard-coding one contest
problem into the Harness.  For example:

```json
{
  "type": "answer",
  "mandatory": true,
  "paper_obligations": [
    {
      "kind": "production_plan",
      "placement": "appendix",
      "formats": ["table"],
      "requires_body_reference": true,
      "min_evidence": 1,
      "artifact_contract": {
        "kind": "csv_table",
        "min_rows": 229,
        "required_fields": ["day", "mating_ewes", "pens"]
      }
    },
    {
      "kind": "stochastic_event_flow",
      "placement": "body",
      "formats": ["flowchart"],
      "min_evidence": 1
    }
  ]
}
```

This supports full production schedules, stage populations, pen assignments,
state-transition diagrams, nonanticipative decision rules, simulation
pseudocode, convergence tables, stress scenarios and boundary certificates.

## Body versus technical appendix

The body must keep the direct answer, key equations, representative results,
validation conclusion and management interpretation.  The technical appendix
holds long daily/scenario tables, full checker traces, complete stress tables
and machine field locks.  Any appendix obligation with
`requires_body_reference: true` must have a literal reference in the body.

The labels for authoritative/machine numeric lock tables are appendix-only.
Their presence in `paper/final.md` is a hard failure.  This keeps machine schema
noise out of the paper while preserving auditability.

## Page target

The competition body targets 18–25 pages.  The renderer records a best-effort
`page_count`; an out-of-range count produces a warning in
`paper_coverage.json`.  Page count is deliberately advisory because padding is
not a substitute for semantic completeness.  Missing obligations remain hard
S6 failures at any length.

## Commands

```powershell
modelharness profile use cumcm --project .
modelharness paper contract-init --project .
# complete paper/content_coverage.json and the paper artifacts
modelharness paper content-audit --project .
modelharness paper build --project .
modelharness paper audit --project .
```

`paper audit` combines render integrity with content-contract auditing when the
active profile enables both.

## 5a Decision-variable manifest and decision claims

`results/decision_variable_manifest.json` is the single authority on what the
model actually decided, as opposed to what the paper says it decided:

```json
{
  "schema": 1,
  "variables": [
    {
      "id": "x.mating_batch",
      "role": "decision",
      "statement": "每日开配母羊批次数，由求解器决定",
      "source": {"path": "results/solver_model.json", "sha256": "..."}
    }
  ]
}
```

`role` must be one of `decision`, `parameter` or `derived`.
`modelharness.optimization.audit_decision_manifest` runs whenever the manifest
exists (and inside `audit_optimization` for the solver/decision phases): every
entry needs a unique id, a legal role, a nonempty statement and a `source`
whose `path` exists with a matching `sha256`.

The content contract gains two optional sections:

- `decision_claim_phrases`: joint-decision phrases to police in the paper;
  the template defaults are `联合优化`, `同时决策` and `联合决策`;
- `decision_claims`: `[{"phrase": ..., "variable_ids": [...]}]` declaring
  which manifest variables back each phrase.

`audit_paper_content` scans `paper/final.md` (the contract body) and
`paper/draft.md`.  Every occurrence of a listed phrase must be covered by a
`decision_claims` entry whose `variable_ids` all exist in the manifest with
`role == "decision"`.  A covered phrase whose variables are missing or not
decisions reports `phantom_decision_variable`; an uncovered phrase reports
`undeclared_decision_claim`.  Writing "联合优化 A 与 B" while B was fixed
before solving is therefore a hard failure.

## 5b In-text citations

When the delivery profile sets `require_in_text_citations: true` (the
`cumcm` and `mcm_icm` templates do), `sanitize_report` cross-checks the
References/参考文献 section against the body:

- every numbered entry `[k]` in the references section must be cited at least
  once in the body before that section, as `[k]`, `[k,m]` or `[k-m]`;
  each missing one is a `uncited_reference` violation carrying the entry
  number;
- every in-text citation number without a matching numbered entry is a
  `citation_without_entry` violation.

Bracketed numbers followed by `(` (markdown links), bracket groups
containing `0` (interval notation such as `[0, 1]`) and bracket groups
directly adjacent to `$` (math-mode intervals such as `$[10,16]$`) are not
treated as citations.  The check is skipped entirely when the paper has no
references heading.

## 5c Headline reproduction chain

`config/claim_bindings.json` entries may declare a `regenerator`: the
project-relative path of the script that regenerates the bound number.  When
the content contract sets `require_regenerator_for_headline_claims: true`
(the template default), `audit_paper_content` requires every binding marked
`headline: true` to name a `regenerator` whose script file exists; otherwise
it reports `broken_reproduction_chain` for that claim id.  A headline number
whose producing script has been deleted or was never written can no longer
pass the S6 gate.

