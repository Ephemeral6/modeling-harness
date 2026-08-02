from pathlib import Path

from modelharness.narrative import audit_final


root = Path(__file__).resolve().parent.parent
errors = audit_final(root, "paper/final.md")
raise SystemExit("\n".join(errors) if errors else 0)
