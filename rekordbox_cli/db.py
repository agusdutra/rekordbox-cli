"""Database connection and safety helpers."""

import shutil
from datetime import datetime
from pathlib import Path

from pyrekordbox import Rekordbox6Database, get_config


def get_database() -> Rekordbox6Database:
    """Open the rekordbox master.db (auto-discovers path and decrypts)."""
    return Rekordbox6Database()


def get_db_path() -> Path:
    """Find the master.db path from pyrekordbox config."""
    config = get_config()
    return Path(config["db_path"])


def create_backup(label: str) -> Path:
    """Create a timestamped backup of master.db before modifications."""
    db_path = get_db_path()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"master.db.backup_{label}_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def safe_commit(db: Rekordbox6Database, label: str = "edit") -> Path:
    """Backup then commit. Raises RuntimeError if rekordbox is running."""
    backup_path = create_backup(label)
    db.commit()
    return backup_path
