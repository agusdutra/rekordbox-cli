"""Database connection and safety helpers."""

import shutil
from datetime import datetime
from pathlib import Path

from pyrekordbox import Rekordbox6Database


def get_database() -> Rekordbox6Database:
    """Open the rekordbox master.db (auto-discovers path and decrypts)."""
    return Rekordbox6Database()


def get_db_path() -> Path:
    """Find the master.db path from pyrekordbox config."""
    from pyrekordbox import get_config
    # Try rekordbox7 first, then rekordbox6
    for section in ("rekordbox7", "rekordbox6"):
        try:
            db_path = get_config(section, "db_path")
            if db_path:
                return Path(db_path)
        except (KeyError, TypeError):
            continue
    # Fallback to known default
    return Path.home() / "Library/Pioneer/rekordbox/master.db"


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
