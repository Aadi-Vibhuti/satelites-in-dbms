"""Configuration management for API access and database settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"

DEFAULT_CONFIG: dict[str, Any] = {
    "nasa_api_key": "DEMO_KEY",
    "n2yo_api_key": "",
    "spacetrack_identity": "",
    "spacetrack_password": "",
    "default_api_source": "all",
    "db_host": "127.0.0.1",
    "db_port": 3307,
    "db_user": "ois",
    "db_password": "ois",
    "db_name": "ois_db",
    "db_storage_dir": str(ROOT / "sql"),
}


def load_config() -> dict[str, Any]:
    """Load configuration from config.json, with fallback to .env and defaults."""
    cfg = dict(DEFAULT_CONFIG)

    # Read from .env if present
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "OIS_DB_HOST":
                    cfg["db_host"] = v
                elif k == "OIS_DB_PORT" and v.isdigit():
                    cfg["db_port"] = int(v)
                elif k == "OIS_DB_USER":
                    cfg["db_user"] = v
                elif k == "OIS_DB_PASSWORD":
                    cfg["db_password"] = v
                elif k == "OIS_DB_NAME":
                    cfg["db_name"] = v
                elif k == "NASA_API_KEY":
                    cfg["nasa_api_key"] = v
                elif k == "N2YO_API_KEY":
                    cfg["n2yo_api_key"] = v
                elif k == "SPACETRACK_IDENTITY":
                    cfg["spacetrack_identity"] = v
                elif k == "SPACETRACK_PASSWORD":
                    cfg["spacetrack_password"] = v
        except Exception:
            pass

    # Overlay config.json
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception:
            pass

    return cfg


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Persist updated configuration to config.json and .env."""
    cfg = load_config()
    cfg.update(updates)

    # Save to config.json
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"Warning: could not write config.json: {exc}")

    # Synchronize relevant keys back into .env
    env_lines = [
        f"OIS_DB_HOST={cfg.get('db_host', '127.0.0.1')}",
        f"OIS_DB_PORT={cfg.get('db_port', 3307)}",
        f"OIS_DB_USER={cfg.get('db_user', 'ois')}",
        f"OIS_DB_PASSWORD={cfg.get('db_password', 'ois')}",
        f"OIS_DB_NAME={cfg.get('db_name', 'ois_db')}",
    ]
    if cfg.get("nasa_api_key"):
        env_lines.append(f"NASA_API_KEY={cfg['nasa_api_key']}")
    if cfg.get("n2yo_api_key"):
        env_lines.append(f"N2YO_API_KEY={cfg['n2yo_api_key']}")
    if cfg.get("spacetrack_identity"):
        env_lines.append(f"SPACETRACK_IDENTITY={cfg['spacetrack_identity']}")
    if cfg.get("spacetrack_password"):
        env_lines.append(f"SPACETRACK_PASSWORD={cfg['spacetrack_password']}")

    try:
        ENV_PATH.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"Warning: could not write .env: {exc}")

    # Also update os.environ
    os.environ["OIS_DB_HOST"] = str(cfg["db_host"])
    os.environ["OIS_DB_PORT"] = str(cfg["db_port"])
    os.environ["OIS_DB_USER"] = str(cfg["db_user"])
    os.environ["OIS_DB_PASSWORD"] = str(cfg["db_password"])
    os.environ["OIS_DB_NAME"] = str(cfg["db_name"])

    return cfg
