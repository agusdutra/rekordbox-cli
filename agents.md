# rekordbox-cli

A command-line tool for managing and enriching your rekordbox DJ library.

## Overview

rekordbox-cli reads and writes to the rekordbox 6/7 encrypted SQLite database (`master.db`) via pyrekordbox, providing batch operations that are tedious or impossible through the rekordbox GUI.

## Architecture

```
rekordbox_cli/
├── main.py          # CLI entry point (click groups)
├── db.py            # Database connection & helpers
├── genre.py         # Genre classification & tagging
└── mappings.py      # Playlist→genre, artist→genre, keyword→genre maps
```

- **Database layer** (`db.py`): Handles connecting to the encrypted master.db, backup creation, and safe commits (refuses if rekordbox is running).
- **Genre module** (`genre.py`): Classifies tracks using playlist membership, artist matching, and title keyword detection. Applies genre tags in bulk.
- **Mappings** (`mappings.py`): User-editable dictionaries that control how playlists, artists, and keywords map to genre names.

## Features

### `rb genre set` — Auto-tag genres on your collection

Assigns genre metadata to untagged tracks using three signals (in priority order):
1. **Playlist membership** — tracks in "Reggaeton" playlist get tagged Reggaeton
2. **Artist name matching** — known artists map to genres
3. **Title keywords** — words like "cumbia", "house", "guaracha" in track titles

```bash
rb genre set          # Tag all untagged tracks
rb genre set --dry-run  # Preview without writing
rb genre set --force    # Re-tag all tracks (overwrite existing)
```

## Development

```bash
# Install in dev mode
pip install -e .

# Run
rb genre set --dry-run
```

## Safety

- Always creates a backup before writing (`master.db.backup_before_<operation>`)
- Refuses to commit if rekordbox is running
- Use `--dry-run` to preview changes

## Adding new features

New commands go under `rekordbox_cli/` as modules, registered via click groups in `main.py`. Each command should:
1. Open the DB via `db.get_database()`
2. Perform read/classification logic
3. Show a preview/summary
4. Commit via `db.safe_commit()` (handles backup + running check)
