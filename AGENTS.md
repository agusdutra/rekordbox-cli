# rekordbox-cli

Public repo: https://github.com/agusdutra/rekordbox-cli

## Overview

`rekordbox-cli` manages and enriches a rekordbox 6/7 library by reading and writing the encrypted SQLite `master.db` via `pyrekordbox`.

## CLI surface

- `rb genre set`, `rb genre show`, `rb genre sources`, `rb genre sync`
- `rb playlist sort`
- `rb collection dedupe`
- `rb enrich`
- `rb analyze`
- `rb history latest`
- `rb spotify login`, `rb spotify playlists`, `rb spotify diff`, `rb spotify missing`, `rb spotify to-rekordbox`

## Key files

```
rekordbox_cli/
├── main.py          # Click entry point and command wiring
├── db.py            # Database connection, backup, commit safety
├── genre.py         # Genre classification and tagging
├── enrich.py        # Metadata enrichment
├── essentia_analysis.py # Audio analysis and feature extraction
└── mappings.py      # Playlist/artist/keyword mappings
```

## Usage notes

### Install

```bash
pip install -e .
```

### Common commands

```bash
rb --help
./start --help
rb genre set --dry-run
rb genre set --force
rb genre show
rb genre sync --dry-run
rb playlist sort "playlist name" --dry-run
rb playlist sort "folder/playlist name"
rb playlist sort "playlist name" --verbose
rb collection dedupe --dry-run
rb collection dedupe --dry-run --include-tentative --verbose
rb enrich --dry-run
rb analyze -n 100
rb history latest
rb spotify diff "playlist name"
rb spotify missing "playlist name"
rb spotify to-rekordbox "playlist name"
```

### Configuration

Use `.env` or shell variables for API credentials:

- `LASTFM_API_KEY`
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `DISCOGS_TOKEN`

## Safety

- Backups are created before write operations.
- Commits are refused if rekordbox is running.
- Prefer `--dry-run` before changing the database or files.

## Editing guidance

- Keep Click command patterns consistent with `main.py`.
- Update `README.md` when user-facing commands or options change.
- Reuse `db.safe_commit()` for write paths.
