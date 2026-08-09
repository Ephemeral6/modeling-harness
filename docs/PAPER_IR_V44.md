# Paper IR 4.4

Harness papers used to be assembled by string concatenation: agents wrote
`paper/draft.md` directly and hand-copied headline numbers into prose.  Every
re-run of the pipeline re-introduced the same class of defect — a stale copied
number surviving next to a fresh binding.  Paper IR 4.4 removes the root cause:
the paper becomes a compiled artifact and prose can no longer carry unbound
numbers.

## Design boundary

- The agent still chooses the narrative, section structure and wording.
- The authoritative source is `paper/src/`; `paper/draft.md` and
  `paper/paper_ir.json` are generated artifacts and must never be edited by
  hand.
- Numeric truth stays where it already lives: `config/claim_bindings.json`
  evaluated by `modelharness.claims`.  The compiler only *references* claims,
  it never re-derives them.
- Adoption is opt-in per project: without `paper/src/manifest.json` the
  `compile --check` gate is a no-op, so legacy projects keep working.

## Source of truth layout

| File | Role |
|---|---|
| `paper/src/manifest.json` | `{schema: 1, sections: [{file, title?}]}`; section order is manifest order |
| `paper/src/NN_名称.md` | One Markdown source per section; the `NN_` prefix is a naming habit, not the ordering key |
| `config/claim_bindings.json` | Authoritative numeric bindings (unchanged contract) |
| `config/paper_content_contract.json` | Optional `number_whitelist: [{value, reason}]`; a missing key means an empty whitelist |
| `results/decision_variable_manifest.json` | `{schema: 1, variables: [{id, role, statement, source}]}`; read tolerantly, absence makes every `{decision:}` reference unbound |
| `paper/paper_ir.json` | Generated block tree (heading/prose/equation/table/pseudocode plus inline claim_number/citation/decision nodes) |
| `paper/draft.md` | Deterministically rendered output; byte-identical across recompiles of the same input |

## Typed placeholders

| Placeholder | Renders to | Failure mode it removes |
|---|---|---|
| `{num:claim_id}` | The claim's locked display string from binding evaluation | Hand-copied numbers drifting from re-run results |
| `{ev:evidence_id}` | `[[evidence_id]]`, the marker `narrative audit` already verifies | Unverifiable citations |
| `{decision:var_id}` | The variable's `statement` from the decision manifest | Papers answering a different decision variable than the model |

Changing one binding value and recompiling updates every occurrence in the
paper at once; there is no second copy to forget.

## Lint rules

Every error carries `file:line`.  A non-empty lint report exits 1 and blocks
all output writing.

| Rule | Trigger |
|---|---|
| `bare_number_in_prose` | A digit sequence in prose, front matter or a **table** that is not produced by a placeholder |
| `unbound_claim` | `{num:X}` where `X` has no binding |
| `stale_claim_value` | `{num:X}` where the binding evaluation is not `valid` (drift, missing artifact, type or unit error) |
| `cross_caliber_arithmetic` | Two `{num:}` placeholders on one prose line with an arithmetic expression between them (`+ - ± × ÷ * / %`, 相差/之和/之差/合计/百分之) and neither binding declares `derived_from`; a `/` flanked by CJK characters on both sides (`只/年` unit form) does not count |
| `unbound_decision_reference` | `{decision:X}` where the manifest or the variable is missing |
| `invalid_number_whitelist` | A whitelist entry without a non-empty `reason` (the entry grants no exemption) |
| `manifest_invalid` / `section_missing` | Broken manifest schema or a listed section file that does not exist |

### Which blocks are bound

`prose`, `front_matter` and `table` are **bound blocks**: they are linted for
bare numbers *and* their placeholders are substituted by `render_document`.
A table is body text a reader takes as the answer, so a result parked in a
cell is exactly as unbound as one parked in a sentence — 4.7 closed that hole
after an end-to-end run shipped a ±1% scenario table whose three result
numbers reached `paper/draft.md` under no binding at all.

`equation` (`$$…$$`) and fenced `pseudocode` stay **out** on purpose: their
digits are coefficients, exponents, indices, horizons and code literals
(`365`, `229`, `HORIZON = 229`), not conclusions, and forcing them through
`{num:}` would either mangle the math or invite a whitelist so broad that it
would neuter the rule everywhere else. Numbers that a reader will quote as a
result belong in prose or a table, both of which are bound.

### Bare-number exemptions

Numbers are legal in a bound block only when they cannot be a result claim:

1. **Years**: standalone `1900`–`2099` (e.g. `2023 年`).
2. **Identifier-adjacent digits**: digits touching ASCII letters, `_` or `.`
   (`Q2`, `fig1`, `10_main.md`, `v4.3`) are labels, not values.
3. **Ordered-list markers**: a leading `1. ` or `1) `.
4. **Table row indices**: the first column of a table, and only when its body
   rows spell the ordinals `1, 2, 3, …` in order over at least two rows. One
   stray integer in column 1 is still linted, so a result cannot be hidden by
   moving it left.
5. **Inline math and inline code**: `$...$` and `` `...` `` spans.
6. **Whitelisted constants**: `number_whitelist` entries with a `reason`,
   meant for problem-statement constants and printed structural quantities
   (`112` pens, a batch table's day offsets), never for computed results.

Everything else must go through `{num:}`. Units carry no digits of their own
(`只/年` in a header cell), so they are unaffected.

## Compilation pipeline

```text
paper/src/manifest.json + paper/src/*.md
  -> block tree (front_matter | heading | prose | equation | table | pseudocode)
  -> lint (bindings, whitelist, decision manifest)
  -> paper/paper_ir.json          (schema 1, timestamp-free)
  -> paper/draft.md               (LF newlines, deterministic)
```

`compile` refuses to write when lint fails; `compile --check` never writes,
not even `results/claim_values.json` (bindings are evaluated in memory).
Determinism is a contract: compiling the same source twice must produce
byte-identical `draft.md` and `paper_ir.json`.

## Commands

```powershell
modelharness paper compile --check --project .   # lint only, exit 1 on findings
modelharness paper compile --project .           # lint, then write IR + draft
```

The S6 stage gate additionally runs `paper compile --check`, so a paper with
hand-copied numbers can no longer pass the final milestone.  Projects that
have not adopted `paper/src/` pass this check trivially.

## What stays out of scope

The compiler does not replace the existing audits: `narrative audit` still
verifies `[[evidence]]` markers against the evidence graph, and
`paper content-audit` still enforces exposition obligations.  Paper IR only
guarantees that what reaches those audits was compiled from bound sources,
not hand-assembled.
