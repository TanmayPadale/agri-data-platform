"""Configuration shared by every entry point.

Every way this code runs (your terminal, an Airflow task, a Kubernetes pod, CI)
gets its settings the same way: from environment variables. The `.env` file is
only a laptop convenience. It is loaded here without overriding anything that is
already set, so a value exported in your shell or injected by Kubernetes wins.

Settings are read through small functions instead of module-level constants, so
a test can change an environment variable and see the new value immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Read KEY=VALUE lines into os.environ, keeping any value that is already set."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


def agri_dsn() -> str:
    """Postgres connection string for the owner role (see docker-compose.yml)."""
    return os.environ.get("AGRI_DSN", "postgresql://agri:agri@localhost:5432/agri")


def data_dir() -> Path:
    """Root folder for landed files, e.g. data/raw/weather/dt=2026-10-01/."""
    path = Path(os.environ.get("AGRI_DATA_DIR", "data"))
    return path if path.is_absolute() else REPO_ROOT / path
