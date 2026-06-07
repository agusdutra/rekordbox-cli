"""rekordbox-cli: CLI entry point."""

import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import click

from .db import get_database, safe_commit
from .genre import print_summary, set_genres


def _load_env():
    """Load .env file from project root if it exists."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_env()


@click.group()
def cli():
    """rekordbox-cli — manage and enrich your rekordbox library."""
    pass


def _copy_to_clipboard(text: str) -> str:
    """Copy text to OS clipboard and return the command used."""
    commands = [
        ("pbcopy", ["pbcopy"]),
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
        ("clip", ["clip"]),
    ]
    errors = []

    for label, command in commands:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(command, input=text, text=True, check=True, capture_output=True)
            return label
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{label}: {exc}")

    if errors:
        raise RuntimeError(f"Failed to copy to clipboard ({'; '.join(errors)})")
    raise RuntimeError("No supported clipboard command found (tried pbcopy, wl-copy, xclip, xsel, clip).")


def _find_rekordbox_playlist(playlists, playlist_name):
    """Find first rekordbox playlist by case-insensitive fuzzy name match."""
    for playlist in playlists:
        name = playlist.Name or ""
        if playlist_name.lower() in name.lower():
            return playlist
    return None


def _parse_sort_priority(sort_by: str) -> list[str]:
    """Parse and validate playlist sort priorities."""
    priorities = [p.strip().lower() for p in sort_by.split(",") if p.strip()]
    if len(priorities) != 3 or set(priorities) != {"genre", "key", "bpm"}:
        raise click.ClickException(
            "Invalid --by value. Use all fields once, e.g. 'genre,key,bpm' or 'bpm,key,genre'."
        )
    return priorities


def _playlist_track_sort_fields(content):
    """Extract normalized sortable fields from a rekordbox content entry."""
    genre_name = content.Genre.Name.strip() if content and content.Genre and content.Genre.Name else ""
    key_name = content.Key.ScaleName.strip() if content and content.Key and content.Key.ScaleName else ""
    bpm_value = float(content.BPM) if content and content.BPM else None
    artist = content.Artist.Name.strip() if content and content.Artist and content.Artist.Name else ""
    title = content.Title.strip() if content and content.Title else ""

    return {
        "genre": genre_name,
        "key": key_name,
        "bpm": bpm_value,
        "artist": artist,
        "title": title,
    }


def _playlist_sort_key(fields: dict, priorities: list[str]):
    """Build tuple sort key from selected priority order."""
    parts = []
    for priority in priorities:
        if priority == "genre":
            genre = fields["genre"].lower()
            parts.append((0, genre) if genre else (1, ""))
        elif priority == "key":
            key = fields["key"].lower()
            parts.append((0, key) if key else (1, ""))
        elif priority == "bpm":
            bpm = fields["bpm"]
            parts.append((0, bpm) if bpm is not None else (1, float("inf")))

    parts.append(fields["artist"].lower())
    parts.append(fields["title"].lower())
    return tuple(parts)


@cli.group()
def genre():
    """Genre tagging commands."""
    pass


@genre.command("set")
@click.option("--dry-run", is_flag=True, help="Preview changes without writing.")
@click.option("--force", is_flag=True, help="Re-tag all tracks, not just untagged.")
@click.option(
    "--lastfm-key",
    envvar="LASTFM_API_KEY",
    help="Last.fm API key (or set LASTFM_API_KEY env var).",
)
@click.option(
    "--spotify-id",
    envvar="SPOTIFY_CLIENT_ID",
    help="Spotify Client ID (or set SPOTIFY_CLIENT_ID env var).",
)
@click.option(
    "--spotify-secret",
    envvar="SPOTIFY_CLIENT_SECRET",
    help="Spotify Client Secret (or set SPOTIFY_CLIENT_SECRET env var).",
)
@click.option(
    "--discogs-token",
    envvar="DISCOGS_TOKEN",
    help="Discogs personal access token (or set DISCOGS_TOKEN env var).",
)
@click.option(
    "--source",
    type=click.Choice(["apple", "discogs", "spotify", "lastfm", "local", "all"]),
    default="all",
    help="Primary genre source (default: all = Apple→Discogs→Spotify→Last.fm→local).",
)
@click.option("--no-api", is_flag=True, help="Skip all API lookups, use local rules only.")
@click.option("--verbose", "-v", is_flag=True, help="Show per-track classification details.")
def genre_set(dry_run, force, lastfm_key, spotify_id, spotify_secret, discogs_token, source, no_api, verbose):
    """Auto-tag genres using Apple Music/Discogs/Spotify/Last.fm with local rules as fallback.

    Priority order (--source=all): Apple Music → Discogs → Spotify → Last.fm → local rules.
    """
    click.echo("Opening rekordbox database...")
    db = get_database()

    lastfm_client = None
    spotify_client = None
    discogs_client = None
    apple_client = None

    if not no_api:
        if source in ("all", "apple"):
            from .apple_music import AppleMusicClient
            apple_client = AppleMusicClient()
            click.echo("Apple Music/iTunes API enabled (no key needed).")

        if source in ("all", "discogs") and discogs_token:
            from .discogs import DiscogsClient
            discogs_client = DiscogsClient(discogs_token)
            click.echo("Discogs API enabled.")

        if source in ("all", "spotify") and spotify_id and spotify_secret:
            from .spotify import SpotifyClient
            spotify_client = SpotifyClient(spotify_id, spotify_secret)
            click.echo("Spotify API enabled.")

        if source in ("all", "lastfm") and lastfm_key:
            from .lastfm import LastFmClient
            lastfm_client = LastFmClient(lastfm_key)
            click.echo("Last.fm API enabled.")

        if not any([apple_client, discogs_client, spotify_client, lastfm_client]):
            click.echo(click.style("⚠ No API clients available. Using local rules only.", fg="yellow"))

    click.echo("Classifying tracks...")
    result = set_genres(
        db,
        dry_run=dry_run,
        force=force,
        lastfm_client=lastfm_client,
        spotify_client=spotify_client,
        discogs_client=discogs_client,
        apple_client=apple_client,
        verbose=verbose,
    )
    print_summary(result, dry_run=dry_run)

    if dry_run:
        click.echo("\nNo changes written (dry run).")
        db.close()
        return

    if result["assigned"] == 0:
        click.echo("\nNothing to update.")
        db.close()
        return

    click.echo()
    if click.confirm("Apply these changes?"):
        backup = safe_commit(db, "genre")
        click.echo(click.style(f"\n✓ Saved! Backup at: {backup}", fg="green"))
    else:
        click.echo("Aborted.")

    db.close()


@genre.command("sources")
def genre_sources():
    """Show available genre sources and their status."""
    click.echo("\nGenre sources (priority order):\n")

    sources = [
        ("Apple Music", "No key needed (free iTunes API)", "Track-level genres: House, Urbano Latino, Música Tropical, etc."),
        ("Discogs", "DISCOGS_TOKEN", "Styles: Cumbia Villera, Italo Disco, Deep House, etc."),
        ("Spotify", "SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET", "Artist-level genres (broad)"),
        ("Last.fm", "LASTFM_API_KEY", "Community tags (noisy but diverse)"),
        ("Essentia", "No key needed (local ML)", "Audio analysis: energy, mood, danceability, BPM, key"),
        ("Local rules", "None", "Playlist/artist/keyword matching"),
    ]

    for name, key_info, description in sources:
        if key_info in ("No key needed (free iTunes API)", "No key needed (local ML)", "None"):
            status = click.style("✓ Available", fg="green")
        else:
            keys = key_info.split(" + ")
            available = all(os.environ.get(k) for k in keys)
            status = click.style("✓ Configured", fg="green") if available else click.style("✗ Missing key", fg="red")

        click.echo(f"  {status}  {name:<12s} — {description}")
        if key_info not in ("No key needed (free iTunes API)", "No key needed (local ML)", "None"):
            click.echo(f"             Key: {key_info}")
    click.echo()


@genre.command("show")
def genre_show():
    """Show current genre distribution in your collection."""
    db = get_database()
    tracks = db.get_content().all()

    from collections import Counter

    genres = Counter(t.Genre.Name if t.Genre else "(untagged)" for t in tracks)

    click.echo(f"\nGenre distribution ({len(tracks)} tracks):\n")
    for genre_name, count in genres.most_common():
        bar = "█" * (count // 5)
        click.echo(f"  {count:4d}  {genre_name:<20s} {bar}")

    db.close()


@genre.command("sync")
@click.option("--dry-run", is_flag=True, help="Preview without writing to files.")
@click.option("--verbose", "-v", is_flag=True, help="Show per-file details.")
def genre_sync(dry_run, verbose):
    """Write genre tags from rekordbox DB into the actual audio files."""
    from .tagger import sync_genres_to_files

    click.echo("Opening rekordbox database...")
    db = get_database()

    click.echo("Syncing genres to audio files...")
    result = sync_genres_to_files(db, dry_run=dry_run, verbose=verbose)

    prefix = "[DRY RUN] " if dry_run else ""
    click.echo(f"\n{prefix}File tagging results:")
    click.echo(f"  Total local files: {result['total']}")
    click.echo(click.style(f"  ✓ Written: {result['written']}", fg="green"))
    if result["skipped"]:
        click.echo(click.style(f"  ⊘ Skipped: {result['skipped']}", fg="yellow"))
    if result["failed"]:
        click.echo(click.style(f"  ✗ Failed: {result['failed']}", fg="red"))

    if dry_run:
        click.echo("\nNo files modified (dry run).")

    db.close()


@cli.group()
def playlist():
    """Playlist commands."""
    pass


@playlist.command("sort")
@click.argument("playlist_name")
@click.option(
    "--by",
    "sort_by",
    default="genre,key,bpm",
    help="Sort priority using all of: genre,key,bpm (default: genre,key,bpm).",
)
@click.option("--dry-run", is_flag=True, help="Preview sorting without writing.")
def playlist_sort(playlist_name, sort_by, dry_run):
    """Sort a rekordbox playlist using genre, key, and BPM."""
    from pyrekordbox.db6.tables import DjmdSongPlaylist

    priorities = _parse_sort_priority(sort_by)

    click.echo("Opening rekordbox database...")
    db = get_database()
    try:
        playlists = db.get_playlist().all()
        target_playlist = _find_rekordbox_playlist(playlists, playlist_name)
        if not target_playlist:
            click.echo(click.style(f"✗ Playlist '{playlist_name}' not found.", fg="red"))
            click.echo("Available playlists:")
            for pl in playlists:
                if pl.Name:
                    click.echo(f"  {pl.Name}")
            return

        playlist_songs = (
            db.get_playlist_songs(PlaylistID=target_playlist.ID)
            .order_by(DjmdSongPlaylist.TrackNo.asc())
            .all()
        )
        if not playlist_songs:
            click.echo(click.style(f"✗ Playlist '{target_playlist.Name}' has no tracks.", fg="red"))
            return

        sortable_rows = []
        for ps in playlist_songs:
            fields = _playlist_track_sort_fields(ps.Content)
            sortable_rows.append(
                {
                    "song": ps,
                    "fields": fields,
                    "sort_key": _playlist_sort_key(fields, priorities),
                }
            )

        sorted_rows = sorted(sortable_rows, key=lambda row: row["sort_key"])
        changed = sum(1 for new_pos, row in enumerate(sorted_rows, start=1) if row["song"].TrackNo != new_pos)

        click.echo(f"\nPlaylist: {target_playlist.Name}")
        click.echo(f"Tracks: {len(sorted_rows)}")
        click.echo(f"Sort order: {', '.join(priorities)}")
        click.echo(f"Tracks moved: {changed}")

        if changed == 0:
            click.echo("\nPlaylist is already sorted.")
            return

        if dry_run:
            click.echo("\nNo changes written (dry run).")
            return

        click.echo()
        if not click.confirm(f"Apply sorted order to '{target_playlist.Name}'?"):
            click.echo("Aborted.")
            return

        for new_pos, row in enumerate(sorted_rows, start=1):
            row["song"].TrackNo = new_pos

        backup = safe_commit(db, "playlist_sort")
        click.echo(click.style(f"\n✓ Sorted playlist '{target_playlist.Name}'.", fg="green"))
        click.echo(f"  Backup at: {backup}")
    finally:
        db.close()


@cli.command("enrich")
@click.option("--dry-run", is_flag=True, help="Preview changes without writing.")
@click.option("--force", is_flag=True, help="Update all tracks, not just incomplete ones.")
@click.option("--verbose", "-v", is_flag=True, help="Show per-track details.")
@click.option(
    "--spotify-id", envvar="SPOTIFY_CLIENT_ID",
    help="Spotify Client ID (or set SPOTIFY_CLIENT_ID env var).",
)
@click.option(
    "--spotify-secret", envvar="SPOTIFY_CLIENT_SECRET",
    help="Spotify Client Secret (or set SPOTIFY_CLIENT_SECRET env var).",
)
@click.option(
    "--lastfm-key", envvar="LASTFM_API_KEY",
    help="Last.fm API key (or set LASTFM_API_KEY env var).",
)
def enrich(dry_run, force, verbose, spotify_id, spotify_secret, lastfm_key):
    """Fix and enrich track metadata (title, artist, album, year, label) from Spotify/Last.fm."""
    from .enrich import enrich_tracks, print_enrich_summary

    click.echo("Opening rekordbox database...")
    db = get_database()

    spotify_client = None
    lastfm_client = None

    if spotify_id and spotify_secret:
        from .spotify import SpotifyClient
        spotify_client = SpotifyClient(spotify_id, spotify_secret)
        click.echo("Spotify API enabled.")

    if lastfm_key:
        from .lastfm import LastFmClient
        lastfm_client = LastFmClient(lastfm_key)
        click.echo("Last.fm API enabled.")

    if not spotify_client and not lastfm_client:
        click.echo(click.style(
            "⚠ No API credentials provided. Set them in .env or pass via options.",
            fg="red",
        ))
        db.close()
        return

    click.echo("Enriching track metadata...")
    result = enrich_tracks(
        db,
        spotify_client=spotify_client,
        lastfm_client=lastfm_client,
        dry_run=dry_run,
        verbose=verbose,
        force=force,
    )
    print_enrich_summary(result, dry_run=dry_run)

    if dry_run:
        click.echo("\nNo changes written (dry run).")
        db.close()
        return

    if result["updated"] == 0:
        click.echo("\nNothing to update.")
        db.close()
        return

    click.echo()
    if click.confirm("Apply these changes?"):
        backup = safe_commit(db, "enrich")
        click.echo(click.style(f"\n✓ Saved! Backup at: {backup}", fg="green"))
    else:
        click.echo("Aborted.")

    db.close()


@cli.group()
def history():
    """History commands."""
    pass


@history.command("latest")
@click.option(
    "--playlist-name",
    default=None,
    help="Name for the created playlist (default: 'History - <history name>').",
)
def history_latest(playlist_name):
    """Copy latest history to clipboard, print it, and create a playlist from it."""
    from pyrekordbox.db6.tables import DjmdHistory, DjmdSongHistory

    db = get_database()
    try:
        histories = (
            db.get_history()
            .order_by(DjmdHistory.DateCreated.desc(), DjmdHistory.Seq.desc())
            .all()
        )
        if not histories:
            raise click.ClickException("No history entries found.")

        latest_history = None
        history_songs = []
        for candidate in histories:
            songs = (
                db.get_history_songs(HistoryID=candidate.ID)
                .order_by(DjmdSongHistory.TrackNo.asc())
                .all()
            )
            if songs:
                latest_history = candidate
                history_songs = songs
                break

        if not latest_history:
            raise click.ClickException("No history entries with tracks found.")

        reference_time = history_songs[0].created_at or latest_history.DateCreated
        track_lines = []
        content_ids = []
        for idx, song in enumerate(history_songs, start=1):
            content = song.Content
            artist = content.Artist.Name if content and content.Artist else "Unknown Artist"
            title = content.Title if content and content.Title else "Unknown Title"
            played_at = song.created_at or latest_history.DateCreated
            elapsed = max(0, int((played_at - reference_time).total_seconds())) if (played_at and reference_time) else 0
            if elapsed == 0:
                elapsed_str = "0"
            else:
                hours, rem = divmod(elapsed, 3600)
                minutes, seconds = divmod(rem, 60)
                elapsed_str = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
            skipto = "0:00" if elapsed == 0 else elapsed_str
            track_lines.append(f"{idx:02d}. [{elapsed_str}](#t={skipto}) {artist} - {title}")
            content_ids.append(song.ContentID)

        history_name = latest_history.Name or "Latest History"
        history_text = "\n".join(track_lines)

        click.echo(f"\n{history_name} ({len(track_lines)} tracks):\n")
        click.echo(history_text)

        clipboard_tool = _copy_to_clipboard(history_text)
        click.echo(click.style(f"\n✓ Copied history to clipboard via {clipboard_tool}.", fg="green"))

        desired_playlist_name = playlist_name or f"History - {history_name}"
        existing_names = {p.Name for p in db.get_playlist().all() if p.Name}
        final_playlist_name = desired_playlist_name
        suffix = 2
        while final_playlist_name in existing_names:
            final_playlist_name = f"{desired_playlist_name} ({suffix})"
            suffix += 1

        playlist = db.create_playlist(final_playlist_name)
        for track_no, content_id in enumerate(content_ids, start=1):
            db.add_to_playlist(playlist, content_id, track_no=track_no)

        backup = safe_commit(db, "history_playlist")
        click.echo(
            click.style(
                f"✓ Created playlist '{final_playlist_name}' with {len(content_ids)} tracks.",
                fg="green",
            )
        )
        click.echo(f"  Backup at: {backup}")
    finally:
        db.close()


@cli.group()
def spotify():
    """Spotify integration commands."""
    pass


def _find_spotify_playlist(playlists, playlist_name):
    """Find first Spotify playlist by case-insensitive fuzzy name match."""
    for playlist in playlists:
        if playlist_name.lower() in playlist["name"].lower():
            return playlist
    return None


def _normalize_track_title(title: str) -> str:
    """Normalize track title for matching."""
    return (title or "").lower().strip()


def _clean_track_title(title: str) -> str:
    """Remove common remix/version suffixes for looser matching."""
    normalized = _normalize_track_title(title)
    return normalized.split("(")[0].split("[")[0].strip()


def _build_local_content_index(local_tracks):
    """Build artist+title index for rekordbox content objects."""
    index = defaultdict(list)
    for track in local_tracks:
        artist = (track.Artist.Name if track.Artist else "").lower().strip()
        title = _normalize_track_title(track.Title or "")
        if not artist or not title:
            continue

        clean_title = _clean_track_title(title)
        index[(artist, title)].append(track)
        if clean_title and clean_title != title:
            index[(artist, clean_title)].append(track)
    return index


@spotify.command("login")
def spotify_login():
    """Authenticate with Spotify (opens browser for OAuth)."""
    from .spotify_user import get_spotify_user_client

    click.echo("Opening browser for Spotify login...")
    sp = get_spotify_user_client()
    user = sp.current_user()
    click.echo(click.style(f"✓ Logged in as: {user['display_name']} ({user['id']})", fg="green"))


@spotify.command("playlists")
def spotify_playlists():
    """List your Spotify playlists."""
    from .spotify_user import get_spotify_user_client, get_user_playlists

    sp = get_spotify_user_client()
    playlists = get_user_playlists(sp)

    click.echo(f"\nYour Spotify playlists ({len(playlists)}):\n")
    for pl in playlists:
        click.echo(f"  {pl['tracks']['total']:4d} tracks  {pl['name']}")


@spotify.command("to-rekordbox")
@click.argument("playlist_name")
@click.option(
    "--output",
    "-o",
    default=None,
    help="Name for the rekordbox playlist (default: 'Spotify - <source>').",
)
@click.option("--dry-run", is_flag=True, help="Preview without writing to rekordbox.")
def spotify_to_rekordbox(playlist_name, output, dry_run):
    """Create a rekordbox playlist using tracks already present locally from a Spotify playlist."""
    from .spotify_user import get_spotify_user_client, get_user_playlists, get_playlist_tracks

    click.echo("Connecting to Spotify...")
    sp = get_spotify_user_client()
    playlists = get_user_playlists(sp)

    match = _find_spotify_playlist(playlists, playlist_name)
    if not match:
        click.echo(click.style(f"✗ Playlist '{playlist_name}' not found.", fg="red"))
        click.echo("Available playlists:")
        for pl in playlists:
            click.echo(f"  {pl['name']}")
        return

    click.echo(f"Source playlist: {match['name']} ({match['tracks']['total']} tracks)")
    click.echo("Fetching Spotify tracks...")
    spotify_tracks = get_playlist_tracks(sp, match["id"])

    click.echo("Loading rekordbox library...")
    db = get_database()
    try:
        local_tracks = db.get_content().all()
        local_index = _build_local_content_index(local_tracks)

        matched_tracks = []
        matched_ids = set()
        missing_count = 0

        for sp_track in spotify_tracks:
            sp_artist = (sp_track["artists"][0]["name"] if sp_track.get("artists") else "").lower().strip()
            sp_title = _normalize_track_title(sp_track.get("name", ""))
            sp_clean_title = _clean_track_title(sp_title)

            candidates = local_index.get((sp_artist, sp_title), [])
            if not candidates and sp_clean_title:
                candidates = local_index.get((sp_artist, sp_clean_title), [])

            local_match = next((track for track in candidates if track.ID not in matched_ids), None)
            if local_match:
                matched_tracks.append(local_match)
                matched_ids.add(local_match.ID)
            else:
                missing_count += 1

        if not matched_tracks:
            click.echo(click.style("\n✗ No Spotify tracks were found in your local rekordbox collection.", fg="red"))
            return

        output_name = output or f"Spotify - {match['name']}"
        existing_names = {p.Name for p in db.get_playlist().all() if p.Name}
        final_name = output_name
        suffix = 2
        while final_name in existing_names:
            final_name = f"{output_name} ({suffix})"
            suffix += 1

        click.echo(f"\nMatched local tracks: {len(matched_tracks)}")
        click.echo(f"Missing from local collection: {missing_count}")
        click.echo(f"Target rekordbox playlist: {final_name}")

        if dry_run:
            click.echo("\nNo changes written (dry run).")
            return

        click.echo()
        if not click.confirm(f"Create rekordbox playlist '{final_name}' with {len(matched_tracks)} tracks?"):
            click.echo("Aborted.")
            return

        playlist = db.create_playlist(final_name)
        for track_no, content in enumerate(matched_tracks, start=1):
            db.add_to_playlist(playlist, content.ID, track_no=track_no)

        backup = safe_commit(db, "spotify_to_rekordbox")
        click.echo(click.style(f"\n✓ Created rekordbox playlist '{final_name}' with {len(matched_tracks)} tracks.", fg="green"))
        click.echo(f"  Backup at: {backup}")
    finally:
        db.close()


@spotify.command("diff")
@click.argument("playlist_name")
def spotify_diff(playlist_name):
    """Compare a Spotify playlist with your local rekordbox library.

    Shows which tracks are already local and which are missing.
    """
    from .spotify_user import get_spotify_user_client, get_user_playlists, get_playlist_tracks

    click.echo("Connecting to Spotify...")
    sp = get_spotify_user_client()
    playlists = get_user_playlists(sp)

    # Find the playlist (fuzzy match by name)
    match = None
    for pl in playlists:
        if playlist_name.lower() in pl["name"].lower():
            match = pl
            break

    if not match:
        click.echo(click.style(f"✗ Playlist '{playlist_name}' not found.", fg="red"))
        click.echo("Available playlists:")
        for pl in playlists:
            click.echo(f"  {pl['name']}")
        return

    click.echo(f"Found playlist: {match['name']} ({match['tracks']['total']} tracks)")

    # Get playlist tracks from Spotify
    click.echo("Fetching tracks...")
    spotify_tracks = get_playlist_tracks(sp, match["id"])

    # Get local rekordbox library
    click.echo("Loading rekordbox library...")
    db = get_database()
    local_tracks = db.get_content().all()

    # Build local index (normalized for matching)
    local_index = set()
    for t in local_tracks:
        artist = (t.Artist.Name if t.Artist else "").lower().strip()
        title = (t.Title or "").lower().strip()
        if artist and title:
            local_index.add((artist, title))
            clean_title = title.split("(")[0].split("[")[0].strip()
            if clean_title:
                local_index.add((artist, clean_title))

    # Compare
    found_local = []
    missing = []

    for track in spotify_tracks:
        sp_artist = track["artists"][0]["name"].lower().strip()
        sp_title = track["name"].lower().strip()
        sp_clean_title = sp_title.split("(")[0].split("[")[0].strip()

        is_local = (
            (sp_artist, sp_title) in local_index
            or (sp_artist, sp_clean_title) in local_index
            or any(sp_clean_title == t for _, t in local_index if sp_clean_title in t)
        )

        entry = {
            "artist": track["artists"][0]["name"],
            "title": track["name"],
            "album": track.get("album", {}).get("name", ""),
        }

        if is_local:
            found_local.append(entry)
        else:
            missing.append(entry)

    # Print results
    click.echo(f"\n{'═' * 60}")
    click.echo(f"  Playlist: {match['name']}")
    click.echo(f"  Total tracks: {len(spotify_tracks)}")
    click.echo(click.style(f"  ✓ Already in rekordbox: {len(found_local)}", fg="green"))
    click.echo(click.style(f"  ✗ Missing locally: {len(missing)}", fg="yellow"))
    click.echo(f"{'═' * 60}")

    if found_local:
        click.echo(click.style(f"\n  ✓ LOCAL ({len(found_local)}):", fg="green"))
        for t in found_local[:20]:
            click.echo(f"    {t['artist']} - {t['title']}")
        if len(found_local) > 20:
            click.echo(f"    ... and {len(found_local) - 20} more")

    if missing:
        click.echo(click.style(f"\n  ✗ MISSING ({len(missing)}):", fg="yellow"))
        for t in missing:
            click.echo(f"    {t['artist']} - {t['title']}")

    db.close()


@spotify.command("missing")
@click.argument("playlist_name")
@click.option("--output", "-o", default=None, help="Name for the new Spotify playlist (default: '<source> - Missing').")
def spotify_missing(playlist_name, output):
    """Find tracks missing from rekordbox and add them to a new Spotify playlist."""
    from .spotify_user import get_spotify_user_client, get_user_playlists, get_playlist_tracks

    click.echo("Connecting to Spotify...")
    sp = get_spotify_user_client()
    playlists = get_user_playlists(sp)

    # Find the source playlist
    match = None
    for pl in playlists:
        if playlist_name.lower() in pl["name"].lower():
            match = pl
            break

    if not match:
        click.echo(click.style(f"✗ Playlist '{playlist_name}' not found.", fg="red"))
        click.echo("Available playlists:")
        for pl in playlists:
            click.echo(f"  {pl['name']}")
        return

    click.echo(f"Source playlist: {match['name']} ({match['tracks']['total']} tracks)")

    # Get playlist tracks from Spotify
    click.echo("Fetching Spotify tracks...")
    spotify_tracks = get_playlist_tracks(sp, match["id"])

    # Get local rekordbox library
    click.echo("Loading rekordbox library...")
    db = get_database()
    local_tracks = db.get_content().all()

    # Build local index
    local_index = set()
    for t in local_tracks:
        artist = (t.Artist.Name if t.Artist else "").lower().strip()
        title = (t.Title or "").lower().strip()
        if artist and title:
            local_index.add((artist, title))
            clean_title = title.split("(")[0].split("[")[0].strip()
            if clean_title:
                local_index.add((artist, clean_title))

    # Find missing tracks
    missing_ids = []
    missing_display = []

    for track in spotify_tracks:
        sp_artist = track["artists"][0]["name"].lower().strip()
        sp_title = track["name"].lower().strip()
        sp_clean_title = sp_title.split("(")[0].split("[")[0].strip()

        is_local = (
            (sp_artist, sp_title) in local_index
            or (sp_artist, sp_clean_title) in local_index
            or any(sp_clean_title == t for _, t in local_index if sp_clean_title in t)
        )

        if not is_local:
            missing_ids.append(track["id"])
            missing_display.append(f"{track['artists'][0]['name']} - {track['name']}")

    db.close()

    if not missing_ids:
        click.echo(click.style("\n✓ All tracks are already in your rekordbox library!", fg="green"))
        return

    click.echo(f"\n{len(missing_ids)} tracks missing from rekordbox:")
    for t in missing_display[:15]:
        click.echo(f"  {t}")
    if len(missing_display) > 15:
        click.echo(f"  ... and {len(missing_display) - 15} more")

    # Create the output playlist
    output_name = output or f"{match['name']} - Missing"

    # Check if playlist already exists
    existing_playlist = None
    for pl in playlists:
        if pl["name"] == output_name:
            existing_playlist = pl
            break

    if existing_playlist:
        click.echo(f"\nPlaylist '{output_name}' already exists — checking for duplicates...")

        # Get tracks already in the target playlist
        existing_tracks = get_playlist_tracks(sp, existing_playlist["id"])
        existing_ids = {t["id"] for t in existing_tracks}

        # Filter out tracks already in the playlist
        new_ids = [tid for tid in missing_ids if tid not in existing_ids]

        if not new_ids:
            click.echo(click.style("✓ All missing tracks are already in the playlist!", fg="green"))
            return

        click.echo(f"  {len(missing_ids) - len(new_ids)} already in playlist, {len(new_ids)} new.")
        if not click.confirm(f"Add {len(new_ids)} tracks?"):
            click.echo("Aborted.")
            return

        for i in range(0, len(new_ids), 100):
            batch = new_ids[i:i + 100]
            sp.playlist_add_items(existing_playlist["id"], batch)

        click.echo(click.style(
            f"\n✓ Added {len(new_ids)} tracks to '{output_name}'!",
            fg="green",
        ))
        click.echo(f"  Open: {existing_playlist['external_urls']['spotify']}")
    else:
        click.echo()
        if not click.confirm(f"Create Spotify playlist '{output_name}' with {len(missing_ids)} tracks?"):
            click.echo("Aborted.")
            return

        new_playlist = sp.current_user_playlist_create(
            output_name,
            public=False,
            description=f"Tracks from '{match['name']}' not yet in rekordbox library",
        )

        for i in range(0, len(missing_ids), 100):
            batch = missing_ids[i:i + 100]
            sp.playlist_add_items(new_playlist["id"], batch)

        click.echo(click.style(
            f"\n✓ Created playlist '{output_name}' with {len(missing_ids)} tracks!",
            fg="green",
        ))
        click.echo(f"  Open: {new_playlist['external_urls']['spotify']}")


@cli.command("analyze")
@click.option("--dry-run", is_flag=True, help="Preview analysis without writing to DB.")
@click.option("--limit", "-n", default=0, help="Max tracks to analyze (0 = all).")
@click.option("--verbose", "-v", is_flag=True, help="Show per-track analysis details.")
@click.option("--write-comments", is_flag=True, help="Write mood/energy tags to Comments field.")
def analyze(dry_run, limit, verbose, write_comments):
    """Analyze audio files with Essentia ML for energy, mood, danceability, and genre.

    Results are displayed and optionally written to the Comments field.
    """
    from .essentia_analysis import EssentiaAnalyzer, AudioFeatures
    from urllib.parse import unquote
    from pathlib import Path as P

    click.echo("Opening rekordbox database...")
    db = get_database()
    tracks = db.get_content().all()

    # Filter to local files only
    local_tracks = []
    supported_exts = (".mp3", ".wav", ".flac", ".aiff", ".m4a")
    for t in tracks:
        path = t.FolderPath
        if not path:
            continue
        if path.startswith("spotify:") or path.startswith("soundcloud:"):
            continue
        # Convert file URI to path
        if path.startswith("file://localhost"):
            path = unquote(path.replace("file://localhost", ""))
        elif path.startswith("file://"):
            path = unquote(path[7:])

        base_path = P(path)
        file_path = None

        if base_path.exists() and base_path.is_file():
            file_path = base_path
        else:
            file_name = t.FileNameL or t.FileNameS or ""
            if file_name:
                candidate = base_path / file_name
                if candidate.exists() and candidate.is_file():
                    file_path = candidate

        if file_path and file_path.suffix.lower() in supported_exts:
            local_tracks.append((t, str(file_path)))

    click.echo(f"Found {len(local_tracks)} local audio files.")

    if limit > 0:
        local_tracks = local_tracks[:limit]
        click.echo(f"Analyzing first {limit} tracks...")

    analyzer = EssentiaAnalyzer()
    results = []
    analyzed = 0
    failed = 0

    with click.progressbar(local_tracks, label="Analyzing", show_pos=True) as bar:
        for track, file_path in bar:
            features = analyzer.analyze(file_path)
            if features:
                results.append((track, features))
                analyzed += 1
            else:
                failed += 1

    # Display results
    click.echo(f"\n{'═' * 60}")
    click.echo(f"  Analyzed: {analyzed} tracks  |  Failed: {failed}")
    click.echo(f"{'═' * 60}\n")

    if verbose:
        for track, feat in results[:30]:
            artist = track.Artist.Name if track.Artist else "?"
            title = track.Title or "?"
            click.echo(f"  {artist} - {title}")
            click.echo(f"    BPM: {feat.bpm:.1f} | Energy: {feat.energy_level}/10 | "
                       f"Dance: {feat.danceability:.2f} | Mood: {feat.mood or '?'}")
            if feat.key:
                click.echo(f"    Key: {feat.key} {feat.scale or ''}")
            click.echo()

    # Summary stats
    if results:
        avg_energy = sum(f.energy for _, f in results) / len(results)
        avg_dance = sum(f.danceability for _, f in results) / len(results)
        moods = {}
        for _, f in results:
            if f.mood:
                moods[f.mood] = moods.get(f.mood, 0) + 1

        click.echo(f"  Avg Energy: {avg_energy:.2f} ({round(avg_energy * 10)}/10)")
        click.echo(f"  Avg Danceability: {avg_dance:.2f}")
        if moods:
            click.echo(f"  Mood distribution:")
            for mood, count in sorted(moods.items(), key=lambda x: -x[1]):
                click.echo(f"    {mood:<15s} {count:3d} tracks")

    # Write to Comments if requested
    if write_comments and not dry_run and results:
        click.echo()
        if click.confirm(f"Write mood/energy tags to Comments for {len(results)} tracks?"):
            written = 0
            for track, feat in results:
                tag = feat.mood_tag
                current = track.Commnt or ""
                if tag not in current:
                    # Append to existing comment
                    new_comment = f"{current} [{tag}]".strip() if current else f"[{tag}]"
                    track.Commnt = new_comment
                    written += 1
            if written:
                backup = safe_commit(db, "analyze")
                click.echo(click.style(f"✓ Written {written} comment tags. Backup: {backup}", fg="green"))
            else:
                click.echo("All tracks already have tags.")
    elif write_comments and dry_run:
        click.echo("\n[DRY RUN] Would write comment tags to tracks.")

    db.close()
