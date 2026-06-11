"""Metadata enrichment: fix track title, artist, album, etc. from Spotify/Last.fm."""

import hashlib
import uuid
from collections import defaultdict

import click
from pyrekordbox.db6.tables import DjmdArtist, DjmdAlbum

from .spotify import SpotifyClient
from .lastfm import LastFmClient


def enrich_tracks(db, spotify_client: SpotifyClient = None, lastfm_client: LastFmClient = None,
                  dry_run: bool = False, verbose: bool = False, force: bool = False,
                  playlist_content_ids: set = None) -> dict:
    """Enrich track metadata from Spotify/Last.fm APIs.

    Fixes: Title, Artist, Album, Label, Year, BPM, Key, Duration.

    Args:
        db: Rekordbox6Database instance.
        spotify_client: Optional SpotifyClient.
        lastfm_client: Optional LastFmClient.
        dry_run: If True, don't write changes.
        verbose: If True, print per-track details.
        force: If True, update all tracks (not just ones with missing data).
        playlist_content_ids: If set, only enrich tracks whose ID is in this set.

    Returns:
        dict with stats.
    """
    tracks = db.get_content().all()

    # Restrict to playlist if given
    if playlist_content_ids is not None:
        tracks = [t for t in tracks if t.ID in playlist_content_ids]

    # Target tracks that need enrichment
    if force:
        target = [t for t in tracks if t.FolderPath
                  and not t.FolderPath.startswith("spotify:")
                  and not t.FolderPath.startswith("soundcloud:")]
    else:
        target = [t for t in tracks if _needs_enrichment(t)]

    # Caches for artist/album objects
    artist_cache = {a.Name: a for a in db.get_artist().all()}
    album_cache = {a.Name: a for a in db.get_album().all()}

    updated = 0
    skipped = 0
    failed = 0
    fields_updated = defaultdict(int)

    for t in target:
        artist_name = t.Artist.Name if t.Artist else ""
        title = t.Title or ""

        if not artist_name and not title:
            skipped += 1
            if verbose:
                click.echo(click.style(f"  ⊘ No metadata to search with (ID: {t.ID})", fg="yellow"))
            continue

        # Try Spotify first (richer metadata)
        metadata = None
        source = None
        if spotify_client:
            metadata = _fetch_spotify_metadata(spotify_client, artist_name, title)
            if metadata:
                source = "spotify"

        # Fall back to Last.fm
        if not metadata and lastfm_client:
            metadata = _fetch_lastfm_metadata(lastfm_client, artist_name, title)
            if metadata:
                source = "lastfm"

        if not metadata:
            failed += 1
            if verbose:
                click.echo(click.style(f"  ✗ Not found: {artist_name} - {title}", fg="red"))
            continue

        # Apply updates
        changes = _apply_metadata(db, t, metadata, artist_cache, album_cache, dry_run)
        if changes:
            updated += 1
            for field in changes:
                fields_updated[field] += 1
            if verbose:
                changes_str = ", ".join(f"{f}={v}" for f, v in changes.items())
                click.echo(click.style(
                    f"  ✓ [{source}] {artist_name} - {title} → {changes_str}", fg="green"
                ))
        else:
            skipped += 1
            if verbose:
                click.echo(f"  ≡ Already correct: {artist_name} - {title}")

    return {
        "total": len(target),
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "fields_updated": dict(fields_updated),
    }


def _needs_enrichment(track) -> bool:
    """Check if a track has missing/incomplete metadata worth fixing."""
    if track.FolderPath and (
        track.FolderPath.startswith("spotify:")
        or track.FolderPath.startswith("soundcloud:")
    ):
        return False

    # Missing key fields
    if not track.Title or not track.Artist:
        return True
    if not track.Album:
        return True
    return False


def _fetch_spotify_metadata(client: SpotifyClient, artist: str, title: str) -> dict | None:
    """Fetch track metadata from Spotify."""
    track = client.search_track(artist, title)
    if not track:
        return None

    # Extract metadata
    result = {}

    # Title
    track_name = track.get("name")
    if track_name:
        result["title"] = track_name

    # Artist (primary)
    artists = track.get("artists", [])
    if artists:
        result["artist"] = artists[0].get("name")
        if len(artists) > 1:
            result["composer"] = ", ".join(a["name"] for a in artists[1:])

    # Album
    album = track.get("album", {})
    if album.get("name"):
        result["album"] = album["name"]

    # Year (from album release date)
    release_date = album.get("release_date", "")
    if release_date:
        result["year"] = int(release_date[:4])

    # Duration
    duration_ms = track.get("duration_ms")
    if duration_ms:
        result["duration_ms"] = duration_ms

    # Track number
    track_number = track.get("track_number")
    if track_number:
        result["track_number"] = track_number

    # Label (from album)
    label = album.get("label")
    if label:
        result["label"] = label

    return result if result else None


def _fetch_lastfm_metadata(client: LastFmClient, artist: str, title: str) -> dict | None:
    """Fetch track metadata from Last.fm."""
    data = client._request("track.getInfo", artist=artist, track=title)
    if not data or "track" not in data:
        return None

    track = data["track"]
    result = {}

    # Title
    if track.get("name"):
        result["title"] = track["name"]

    # Artist
    artist_info = track.get("artist", {})
    if artist_info.get("name"):
        result["artist"] = artist_info["name"]

    # Album
    album_info = track.get("album", {})
    if album_info.get("title"):
        result["album"] = album_info["title"]

    # Duration (in ms)
    duration = track.get("duration")
    if duration and int(duration) > 0:
        result["duration_ms"] = int(duration)

    return result if result else None


def _apply_metadata(db, track, metadata: dict, artist_cache: dict, album_cache: dict, dry_run: bool) -> dict:
    """Apply metadata to a track. Returns dict of changed fields and their new values."""
    changes = {}

    # Title
    if metadata.get("title") and (not track.Title or track.Title != metadata["title"]):
        changes["title"] = metadata["title"]
        if not dry_run:
            track.Title = metadata["title"]

    # Artist
    if metadata.get("artist"):
        current_artist = track.Artist.Name if track.Artist else ""
        if not current_artist or current_artist != metadata["artist"]:
            changes["artist"] = metadata["artist"]
            if not dry_run:
                artist_obj = _get_or_create_artist(db, metadata["artist"], artist_cache)
                track.ArtistID = artist_obj.ID

    # Album
    if metadata.get("album"):
        current_album = track.Album.Name if track.Album else ""
        if not current_album or current_album != metadata["album"]:
            changes["album"] = metadata["album"]
            if not dry_run:
                album_obj = _get_or_create_album(db, metadata["album"], album_cache)
                track.AlbumID = album_obj.ID

    # Year
    if metadata.get("year") and (not track.ReleaseYear or track.ReleaseYear != metadata["year"]):
        changes["year"] = metadata["year"]
        if not dry_run:
            track.ReleaseYear = metadata["year"]

    # Label (read-only for now — just report the diff)
    if metadata.get("label") and hasattr(track, "LabelID"):
        current_label = track.LabelName or ""
        if not current_label or current_label != metadata["label"]:
            changes["label"] = metadata["label"]
            if not dry_run:
                track.Label = metadata["label"]

    return changes


def _get_or_create_artist(db, name: str, cache: dict):
    """Get existing artist or create new one."""
    if name in cache:
        return cache[name]

    new_id = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    artist = DjmdArtist(ID=new_id, Name=name, UUID=str(uuid.uuid4()))
    db.session.add(artist)
    cache[name] = artist
    return artist


def _get_or_create_album(db, name: str, cache: dict):
    """Get existing album or create new one."""
    if name in cache:
        return cache[name]

    new_id = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    album = DjmdAlbum(ID=new_id, Name=name, UUID=str(uuid.uuid4()))
    db.session.add(album)
    cache[name] = album
    return album


def print_enrich_summary(result: dict, dry_run: bool = False):
    """Print a formatted summary of enrichment results."""
    prefix = "[DRY RUN] " if dry_run else ""

    click.echo(f"\n{prefix}Metadata enrichment results:")
    click.echo(f"  Tracks processed: {result['total']}")
    click.echo(click.style(f"  ✓ Updated: {result['updated']}", fg="green"))
    click.echo(f"  ≡ Skipped (already correct): {result['skipped']}")
    click.echo(click.style(f"  ✗ Not found: {result['failed']}", fg="yellow"))

    if result["fields_updated"]:
        click.echo()
        click.echo("  Fields updated:")
        for field, count in sorted(result["fields_updated"].items(), key=lambda x: -x[1]):
            click.echo(f"    {count:4d}  {field}")
