from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from app.services import PolicyPulseService


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
DATA_DIR = ROOT / ".runtime_data"

load_dotenv(ROOT / ".env")

service = PolicyPulseService(fixtures_dir=FIXTURES_DIR, data_dir=DATA_DIR)
