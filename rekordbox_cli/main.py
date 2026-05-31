"""rekordbox-cli: CLI entry point."""

import os
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
    "--source",
    type=click.Choice(["spotify", "lastfm", "local"]),
    default="spotify",
    help="Primary genre source (default: spotify).",
)
@click.option("--no-api", is_flag=True, help="Skip all API lookups, use local rules only.")
@click.option("--verbose", "-v", is_flag=True, help="Show per-track classification details.")
def genre_set(dry_run, force, lastfm_key, spotify_id, spotify_secret, source, no_api, verbose):
    """Auto-tag genres using Spotify/Last.fm API with local rules as fallback."""
    click.echo("Opening rekordbox database...")
    db = get_database()

    lastfm_client = None
    spotify_client = None

    if not no_api:
        if source == "spotify" and spotify_id and spotify_secret:
            from .spotify import SpotifyClient
            spotify_client = SpotifyClient(spotify_id, spotify_secret)
            click.echo("Spotify API enabled (primary).")
        elif source == "lastfm" and lastfm_key:
            from .lastfm import LastFmClient
            lastfm_client = LastFmClient(lastfm_key)
            click.echo("Last.fm API enabled (primary).")
        elif source == "spotify" and not (spotify_id and spotify_secret):
            click.echo(click.style(
                "⚠ No Spotify credentials. Set SPOTIFY_CLIENT_ID/SECRET in .env or pass --spotify-id/--spotify-secret.",
                fg="yellow",
            ))
            # Try Last.fm as fallback
            if lastfm_key:
                from .lastfm import LastFmClient
                lastfm_client = LastFmClient(lastfm_key)
                click.echo("Falling back to Last.fm API.")
            else:
                click.echo("Using local rules only.")
        else:
            click.echo("Using local rules only.")

    click.echo("Classifying tracks...")
    result = set_genres(
        db,
        dry_run=dry_run,
        force=force,
        lastfm_client=lastfm_client,
        spotify_client=spotify_client,
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
def spotify():
    """Spotify integration commands."""
    pass


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