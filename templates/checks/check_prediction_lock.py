from pathlib import Path
import hashlib

root = Path(__file__).resolve().parent.parent
prediction = root / "predictions" / "registered.json"
lock = root / "predictions" / "registered.sha256"
if not prediction.is_file() or not lock.is_file():
    raise SystemExit("prediction or lock missing")
actual = hashlib.sha256(prediction.read_bytes()).hexdigest()
expected = lock.read_text(encoding="utf-8").split()[0]
if actual != expected:
    raise SystemExit("prediction lock mismatch")
bindings = root / "config" / "claim_bindings.json"
if bindings.is_file():
    from modelharness.claims import audit_claims

    errors = audit_claims(root, paper="")
    if errors:
        raise SystemExit("\n".join(errors))
raise SystemExit(0)
