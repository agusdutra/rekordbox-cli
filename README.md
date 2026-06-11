# rekordbox-cli

Command-line tools for managing and enriching a rekordbox 6/7 library through the encrypted `master.db`.

## Repository

- GitHub: https://github.com/agusdutra/rekordbox-cli
- Issues and pull requests: use the GitHub repo above

## Requirements

- Python 3.10+
- rekordbox 6 or 7 installed
- Access to your rekordbox database

## Install

```bash
git clone https://github.com/agusdutra/rekordbox-cli.git
cd rekordbox-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Create a `.env` file in the project root or export variables in your shell.

```bash
LASTFM_API_KEY=...
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
DISCOGS_TOKEN=...
```

The CLI also accepts these values as command options where supported.

## Usage

Run commands through the `rb` entry point:

```bash
rb --help
rb genre --help
rb spotify --help
rb playlist --help
rb collection --help
```

### Genre tagging

```bash
rb genre set --dry-run
rb genre set
rb genre set --force
rb genre set --source spotify
rb genre set --no-api
rb genre show
rb genre sources
rb genre sync --dry-run
rb genre sync
```

`genre set` tags tracks using, in order: Apple Music, Discogs, Spotify, Last.fm, then local rules. Edit `rekordbox_cli/mappings.py` to customize playlist, artist, and keyword rules.

### Metadata enrichment

```bash
rb enrich --dry-run
rb enrich
rb enrich --force
```

This updates track metadata such as title, artist, album, year, and label using Spotify and Last.fm.

### Audio analysis

```bash
rb analyze --dry-run
rb analyze -n 100
rb analyze -v
rb analyze --write-comments
```

This analyzes local audio files with Essentia and can write mood/energy tags into rekordbox comments.

### History export

```bash
rb history latest
rb history latest --playlist-name "My Set History"
```

This prints the latest playback history, copies it to your clipboard, and creates a playlist from the tracks.

### Playlist tools

```bash
rb playlist sort "playlist name"
rb playlist sort "Boda Majo/playlist name"
rb playlist sort "playlist name" --by "genre,key,bpm"
rb playlist sort "playlist name" --by "bpm,key,genre"
rb playlist sort "playlist name" --dry-run
rb playlist sort "playlist name" --verbose
```

This sorts a rekordbox playlist using genre, key, and BPM. Use `--by` to choose the priority order. Folder paths are supported with `folder/playlist`. With `--dry-run` or `--verbose`, the sorted track list is printed in the console.

### Collection tools

```bash
rb collection dedupe --dry-run
rb collection dedupe
rb collection dedupe --verbose
rb collection dedupe --dry-run --include-tentative --verbose
```

This removes duplicate collection entries (same local file key), rewires dependent references to the kept track, and then deletes duplicates. Use `--verbose` to print detection diagnostics and candidate duplicate groups. Add `--include-tentative` to also consider likely duplicates by artist+title.

### Spotify tools

```bash
rb spotify login
rb spotify playlists
rb spotify diff "playlist name"
rb spotify missing "playlist name"
rb spotify missing "playlist name" --output "Missing Tracks"
rb spotify to-rekordbox "playlist name"
rb spotify to-rekordbox "playlist name" --output "RB Playlist Name"
rb spotify to-rekordbox "playlist name" --dry-run
```

Use these commands to compare Spotify playlists with your local rekordbox library, create a Spotify playlist of missing tracks, or create a rekordbox playlist from Spotify tracks that are already in your local collection.

## Safety

- Backups are created before write operations
- Commits are refused if rekordbox is running
- Use `--dry-run` before writing changes

## How it works

Genre classification uses three signals:

1. Playlist membership
2. Artist matching
3. Title keywords

The repo also includes API-based lookups for Spotify, Last.fm, Discogs, and Apple Music.

## Development

```bash
pip install -e .
rb --help
```

## License

MIT
