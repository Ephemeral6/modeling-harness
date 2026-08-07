from pathlib import Path

from modelharness.narrative import audit_final
from modelharness.paper import audit_render
from modelharness.paper_content import audit_paper_content
from modelharness.profiles import ProfileService


root = Path(__file__).resolve().parent.parent
errors = audit_final(root, "paper/final.md")
profile = ProfileService(root).active
if profile.get("paper_content_contract"):
    errors.extend(audit_paper_content(root))
if profile.get("paper_delivery"):
    errors.extend(audit_render(root))
raise SystemExit("\n".join(sorted(set(errors))) if errors else 0)
