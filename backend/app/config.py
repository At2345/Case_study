from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIR", str(REPO_ROOT / "data" / "data")))
    reports_dir: Path = Path(os.getenv("REPORTS_DIR", str(REPO_ROOT / "reports")))
    value_at_risk_threshold_gbp: float = float(os.getenv("VAR_THRESHOLD_GBP", "5000"))
    store_dir: Path = Path(os.getenv("STORE_DIR", str(REPO_ROOT / "var")))
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    token_minutes: int = int(os.getenv("TOKEN_MINUTES", "480"))
    cors_origins: tuple = tuple(o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o)
    mongo_uri: str = os.getenv("MONGO_URL", os.getenv("MONGO_URI", ""))
    mongo_db: str = os.getenv("DB_NAME", os.getenv("MONGO_DB", "sop"))
    demo_mode: bool = os.getenv("DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


settings = Settings()
