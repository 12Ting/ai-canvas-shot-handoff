import json
from pathlib import Path

from ai_canvas.main import create_app


target = Path(__file__).parents[2] / "web" / "src" / "api" / "openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
