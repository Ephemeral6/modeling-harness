from pathlib import Path

from modelharness.narrative import audit_final
from modelharness.paper import audit_render
from modelharness.profiles import ProfileService


root = Path(__file__).resolve().parent.parent
errors = audit_final(root, "paper/final.md")
if ProfileService(root).active.get("paper_delivery"):
    errors.extend(audit_render(root))
raise SystemExit("\n".join(sorted(set(errors))) if errors else 0)
