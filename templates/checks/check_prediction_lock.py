from pathlib import Path
import hashlib

root = Path(__file__).resolve().parent.parent
prediction = root / "predictions" / "registered.json"
lock = root / "predictions" / "registered.sha256"
if not prediction.is_file() or not lock.is_file():
    raise SystemExit("prediction or lock missing")
actual = hashlib.sha256(prediction.read_bytes()).hexdigest()
expected = lock.read_text(encoding="utf-8").split()[0]
raise SystemExit(0 if actual == expected else "prediction lock mismatch")

