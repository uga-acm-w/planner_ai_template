# config.py
import toml
from pathlib import Path

class _Config:
    def __init__(self):
        cfg_path = Path(__file__).parent / "config.toml"
        if cfg_path.exists():
            data = toml.load(cfg_path)
        else:
            data = {}
        self.gemini_api_key = data.get("gemini_api_key", "PUT_YOUR_KEY_HERE")
        self.gemini_model = data.get("gemini_model", "models/gemini-1.5-flash")

config = _Config()
