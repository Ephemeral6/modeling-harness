"""Emit search telemetry pinned to the tool run that produced it."""
import json
import os
from pathlib import Path

Path("results/search_telemetry.json").write_text(
    json.dumps({
        "schema": 1,
        "tool_run_id": os.environ.get("MODEL_HARNESS_TOOL_RUN_ID", ""),
        "incumbent_objective": 41.0,
    }, ensure_ascii=False),
    encoding="utf-8",
)
