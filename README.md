# rekordbox-cli

CLI tool for managing and enriching your rekordbox DJ library.

## Install

```bash
pip install -e .
```

Requires Python 3.10+ and rekordbox 6 or 7.

## Usage

```bash
# Preview genre auto-tagging
rb genre set --dry-run

# Apply genre tags to untagged tracks
rb genre set

# Re-tag all tracks (overwrite existing)
rb genre set --force

# Show genre distribution
rb genre show

# Copy latest rekordbox history to clipboard, show it, and create a playlist
rb history latest
```

## How it works

Genre classification uses three signals (in priority order):

1. **Playlist membership** — e.g., tracks in "Reggaeton" playlist → Reggaeton
2. **Artist matching** — known artists mapped to genres
3. **Title keywords** — words like "cumbia", "house" in track titles

Edit `rekordbox_cli/mappings.py` to customize the rules.

## Safety

- Creates a backup before every write operation
- Refuses to commit if rekordbox is running
- Use `--dry-run` to preview

## License

MIT
