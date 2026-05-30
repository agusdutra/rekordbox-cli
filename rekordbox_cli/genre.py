"""Genre classification and tagging for rekordbox tracks."""

import hashlib
import uuid
from collections import defaultdict

import click
from pyrekordbox.db6.tables import DjmdGenre

from .mappings import ARTIST_GENRE, PLAYLIST_GENRE, TITLE_KEYWORDS


def classify_track(track, track_playlists: list[str]) -> str | None:
    """Determine genre for a track using playlist, artist, and title signals.

    Returns genre name or None if unclassifiable.
    """
    genre_name = None

    # 1. Playlist-based (prefer specific genres over generic)
    for pl in track_playlists:
        if pl in PLAYLIST_GENRE:
            g = PLAYLIST_GENRE[pl]
            if genre_name is None or g not in ("Pop", "Party"):
                genre_name = g
            if genre_name not in ("Pop", "Party"):
                break

    # 2. Artist-based (override generic genres)
    if not genre_name or genre_name in ("Pop", "Party"):
        artist = (track.Artist.Name if track.Artist else "").lower()
        for key, g in ARTIST_GENRE.items():
            if key in artist:
                genre_name = g
                break

    # 3. Title keywords
    if not genre_name:
        title = (track.Title or "").lower()
        for key, g in TITLE_KEYWORDS.items():
            if key in title:
                genre_name = g
                break

    return genre_name


def get_or_create_genre(db, name: str, genre_cache: dict) -> DjmdGenre:
    """Get existing genre or create a new one in the database."""
    if name in genre_cache:
        return genre_cache[name]

    new_id = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    genre = DjmdGenre(ID=new_id, Name=name, UUID=str(uuid.uuid4()))
    db.session.add(genre)
    genre_cache[name] = genre
    return genre


def set_genres(db, dry_run: bool = False, force: bool = False) -> dict:
    """Classify and tag genres on tracks.

    Args:
        db: Rekordbox6Database instance.
        dry_run: If True, don't write changes.
        force: If True, re-tag all tracks (not just untagged).

    Returns:
        dict with keys: total, assigned, unclassified, genre_counts
    """
    # Build playlist → track mapping
    playlist_songs = db.get_playlist_songs().all()
    playlists_map = {p.ID: p.Name for p in db.get_playlist().all()}

    track_playlists = defaultdict(list)
    for ps in playlist_songs:
        pname = playlists_map.get(ps.PlaylistID, "?")
        track_playlists[ps.ContentID].append(pname)

    # Load genre cache
    genre_cache = {g.Name: g for g in db.get_genre().all()}

    # Get target tracks
    tracks = db.get_content().all()
    if force:
        target = tracks
    else:
        target = [t for t in tracks if not t.Genre]

    assigned = 0
    genre_counts = defaultdict(int)

    for t in target:
        pls = track_playlists.get(t.ID, [])
        genre_name = classify_track(t, pls)

        if genre_name:
            genre_counts[genre_name] += 1
            if not dry_run:
                genre_obj = get_or_create_genre(db, genre_name, genre_cache)
                t.GenreID = genre_obj.ID
            assigned += 1

    return {
        "total": len(target),
        "assigned": assigned,
        "unclassified": len(target) - assigned,
        "genre_counts": dict(genre_counts),
    }


def print_summary(result: dict, dry_run: bool = False):
    """Print a formatted summary of genre assignment results."""
    prefix = "[DRY RUN] " if dry_run else ""

    click.echo(f"\n{prefix}Genre tagging results:")
    click.echo(f"  Tracks processed: {result['total']}")
    click.echo(click.style(f"  ✓ Assigned: {result['assigned']}", fg="green"))
    click.echo(click.style(f"  ✗ Unclassified: {result['unclassified']}", fg="yellow"))
    click.echo()
    click.echo("  Genre breakdown:")
    for genre, count in sorted(result["genre_counts"].items(), key=lambda x: -x[1]):
        click.echo(f"    {count:4d}  {genre}")
