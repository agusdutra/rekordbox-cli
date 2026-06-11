"""rekordbox-cli: CLI entry point."""

import inspect
import os
import shutil
import subprocess
from collections import defaultdict
from os.path import normpath
from pathlib import Path
from urllib.parse import unquote

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


def _playlist_path(playlist, playlists_by_id):
    """Build full folder path for a rekordbox playlist."""
    parts = []
    current = playlist
    seen_ids = set()
    while current and current.ID not in seen_ids:
        seen_ids.add(current.ID)
        if current.Name:
            parts.append(current.Name)
        parent_id = getattr(current, "ParentID", None)
        current = playlists_by_id.get(parent_id) if parent_id else None
    return "/".join(reversed(parts))


def _find_rekordbox_playlist(playlists, playlist_lookup):
    """Find a rekordbox playlist by full path or fuzzy name."""
    playlists_by_id = {p.ID: p for p in playlists}
    searchable = []
    for playlist in playlists:
        full_path = _playlist_path(playlist, playlists_by_id)
        searchable.append((playlist, full_path, full_path.lower(), (playlist.Name or "").lower()))

    lookup = playlist_lookup.strip().lower()
    if "/" in lookup:
        exact_path_matches = [entry for entry in searchable if entry[2] == lookup]
        if len(exact_path_matches) == 1:
            return exact_path_matches[0][0], None
        if len(exact_path_matches) > 1:
            return None, [entry[1] for entry in exact_path_matches]

        fuzzy_path_matches = [entry for entry in searchable if lookup in entry[2]]
        if len(fuzzy_path_matches) == 1:
            return fuzzy_path_matches[0][0], None
        if len(fuzzy_path_matches) > 1:
            return None, [entry[1] for entry in fuzzy_path_matches]
        return None, None

    name_matches = [entry for entry in searchable if lookup in entry[3]]
    if len(name_matches) == 1:
        return name_matches[0][0], None
    if len(name_matches) > 1:
        return None, [entry[1] for entry in name_matches]
    return None, None


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


def _normalize_collection_path(folder_path: str) -> str | None:
    """Normalize rekordbox folder path for conservative duplicate detection."""
    if not folder_path:
        return None

    path = folder_path.strip()
    if not path:
        return None

    if path.startswith("file://localhost"):
        path = unquote(path.replace("file://localhost", ""))
    elif path.startswith("file://"):
        path = unquote(path[7:])

    if path.startswith(("spotify:", "soundcloud:")):
        return None

    return normpath(path)


def _collection_track_key(track) -> tuple[str | None, str]:
    """Build duplicate-detection key for a collection track."""
    raw_folder_path = (track.FolderPath or "").strip()
    if not raw_folder_path:
        return None, "missing-folder-path"

    normalized_folder = _normalize_collection_path(raw_folder_path)
    if not normalized_folder:
        return None, "unsupported-path"

    file_name = (track.FileNameL or track.FileNameS or "").strip()
    base = Path(normalized_folder)
    if base.suffix:
        return normalized_folder, "file-path"

    if file_name:
        return normpath(str(base / file_name)), "folder-plus-filename"

    return normalized_folder, "folder-only"


def _tentative_track_key(track) -> tuple[tuple[str, str] | None, str]:
    """Build tentative duplicate key using artist+title metadata."""
    artist = (track.Artist.Name if track.Artist and track.Artist.Name else "").strip().lower()
    title = (track.Title or "").strip().lower()
    if not artist or not title:
        return None, "tentative-missing-artist-title"
    return (artist, title), "tentative-artist-title"


def _track_metadata_score(track) -> int:
    """Score content completeness for keeper selection."""
    score = 0
    if track.Title:
        score += 1
    if track.Artist:
        score += 1
    if track.Album:
        score += 1
    if track.Genre:
        score += 1
    if track.Key:
        score += 1
    if track.BPM:
        score += 1
    if track.Commnt:
        score += 1
    return score


def _content_reference_tables():
    """Discover ORM tables that reference ContentID."""
    from pyrekordbox.db6 import tables as db_tables
    from pyrekordbox.db6.tables import DjmdContent

    refs = []
    for _, cls in inspect.getmembers(db_tables, inspect.isclass):
        if not hasattr(cls, "__table__") or cls is DjmdContent:
            continue
        table = cls.__table__
        if "ContentID" in table.columns:
            refs.append(cls)
    return refs


def _choose_keeper(tracks: list, ref_counts: dict) -> tuple:
    """Choose the canonical track to keep from duplicates."""
    def _stock_date_order(track):
        value = getattr(track, "StockDate", None)
        if not value:
            return float("-inf")
        try:
            return -value.timestamp()
        except Exception:
            return float("-inf")

    return max(
        tracks,
        key=lambda t: (
            ref_counts.get(t.ID, 0),
            _track_metadata_score(t),
            _stock_date_order(t),
            -int(t.ID),
        ),
    )


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


@playlist.command("list")
@click.option("--counts", "-c", is_flag=True, help="Show track count per playlist.")
def playlist_list(counts):
    """List all local playlists and folders as a tree."""
    db = get_database()
    all_playlists = db.get_playlist().all()

    if counts:
        from collections import Counter
        songs = db.get_playlist_songs().all()
        track_counts = Counter(s.PlaylistID for s in songs)

    by_parent = {}
    for p in all_playlists:
        by_parent.setdefault(p.ParentID, []).append(p)

    def _print_tree(parent_id, indent=0):
        children = sorted(by_parent.get(parent_id, []), key=lambda p: (p.Attribute != 1, p.Seq or 0, p.Name or ""))
        for p in children:
            is_folder = p.Attribute == 1
            prefix = "  " * indent
            if is_folder:
                click.echo(click.style(f"{prefix}📁 {p.Name}", fg="cyan", bold=True))
                _print_tree(p.ID, indent + 1)
            else:
                if counts:
                    n = track_counts.get(p.ID, 0)
                    click.echo(f"{prefix}▸ {p.Name}  ({n})")
                else:
                    click.echo(f"{prefix}▸ {p.Name}")

    click.echo()
    _print_tree("root")
    click.echo()
    db.close()


@playlist.command("sort")
@click.argument("playlist_name")
@click.option(
    "--by",
    "sort_by",
    default="genre,key,bpm",
    help="Sort priority using all of: genre,key,bpm (default: genre,key,bpm).",
)
@click.option("--dry-run", is_flag=True, help="Preview sorting without writing.")
@click.option("--verbose", "-v", is_flag=True, help="Show sorted track list in console.")
def playlist_sort(playlist_name, sort_by, dry_run, verbose):
    """Sort a rekordbox playlist using genre, key, and BPM."""
    from pyrekordbox.db6.tables import DjmdSongPlaylist

    priorities = _parse_sort_priority(sort_by)

    click.echo("Opening rekordbox database...")
    db = get_database()
    try:
        playlists = db.get_playlist().all()
        target_playlist, ambiguous_paths = _find_rekordbox_playlist(playlists, playlist_name)
        if ambiguous_paths:
            click.echo(click.style(f"✗ Playlist '{playlist_name}' is ambiguous. Use a full path.", fg="red"))
            click.echo("Matching playlists:")
            for path in sorted(set(ambiguous_paths)):
                click.echo(f"  {path}")
            return

        if not target_playlist:
            click.echo(click.style(f"✗ Playlist '{playlist_name}' not found.", fg="red"))
            click.echo("Available playlists (use folder/playlist):")
            playlists_by_id = {p.ID: p for p in playlists}
            for pl in playlists:
                if pl.Name:
                    click.echo(f"  {_playlist_path(pl, playlists_by_id)}")
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

        if dry_run or verbose:
            click.echo("\nSorted playlist order:")
            for new_pos, row in enumerate(sorted_rows, start=1):
                old_pos = row["song"].TrackNo
                marker = "↺" if old_pos != new_pos else " "
                fields = row["fields"]
                artist = fields["artist"] or "Unknown Artist"
                title = fields["title"] or "Unknown Title"
                genre = fields["genre"] or "-"
                key_name = fields["key"] or "-"
                bpm_display = f"{fields['bpm']:.1f}" if fields["bpm"] is not None else "-"
                click.echo(
                    f" {marker} {new_pos:03d} (was {old_pos:03d})  "
                    f"[{genre} | {key_name} | {bpm_display}]  {artist} - {title}"
                )

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


@cli.group()
def collection():
    """Collection commands."""
    pass


@collection.command("dedupe")
@click.option("--dry-run", is_flag=True, help="Preview duplicate cleanup without writing.")
@click.option("--verbose", "-v", is_flag=True, help="Show per-duplicate and per-table details.")
@click.option(
    "--include-tentative",
    is_flag=True,
    help="Also dedupe likely duplicates by artist+title (less strict than path matching).",
)
def collection_dedupe(dry_run, verbose, include_tentative):
    """Remove duplicate collection tracks and remap references to the kept track."""
    from pyrekordbox.db6.tables import DjmdContent

    click.echo("Opening rekordbox database...")
    db = get_database()
    try:
        tracks = db.get_content().all()
        by_path = defaultdict(list)
        by_tentative = defaultdict(list)
        detection_reasons = defaultdict(int)

        for track in tracks:
            collection_key, reason = _collection_track_key(track)
            detection_reasons[reason] += 1
            if collection_key:
                by_path[collection_key].append(track)
            if include_tentative:
                tentative_key, tentative_reason = _tentative_track_key(track)
                detection_reasons[tentative_reason] += 1
                if tentative_key:
                    by_tentative[tentative_key].append(track)

        duplicate_groups = {path: items for path, items in by_path.items() if len(items) > 1}
        tentative_groups = {}
        if include_tentative:
            tracks_in_path_groups = {
                t.ID
                for group in duplicate_groups.values()
                for t in group
            }
            tentative_groups = {
                key: [t for t in items if t.ID not in tracks_in_path_groups]
                for key, items in by_tentative.items()
                if len([t for t in items if t.ID not in tracks_in_path_groups]) > 1
            }
            duplicate_groups.update({f"tentative::{k}": v for k, v in tentative_groups.items()})

        click.echo("\nDuplicate detection:")
        click.echo(f"  Tracks scanned: {len(tracks)}")
        click.echo(f"  Tracks keyed by path: {sum(detection_reasons[r] for r in ('file-path', 'folder-plus-filename', 'folder-only'))}")
        if include_tentative:
            click.echo(f"  Tracks keyed by tentative metadata: {detection_reasons['tentative-artist-title']}")
        click.echo(f"  Unique path keys: {len(by_path)}")
        if include_tentative:
            click.echo(f"  Unique tentative keys: {len(by_tentative)}")
        click.echo(f"  Duplicate path keys: {len([k for k in duplicate_groups if not str(k).startswith('tentative::')])}")
        if include_tentative:
            click.echo(f"  Duplicate tentative keys: {len(tentative_groups)}")
        click.echo(f"  Total duplicate groups: {len(duplicate_groups)}")

        if verbose:
            click.echo("  Key source breakdown:")
            for reason, count in sorted(detection_reasons.items()):
                click.echo(f"    {reason:<22s} {count}")

        if not duplicate_groups:
            if include_tentative:
                click.echo("\nNo duplicates found by path or tentative metadata keys.")
            else:
                click.echo("\nNo duplicates found by file path key.")
            if verbose:
                metadata_groups = defaultdict(list)
                for track in tracks:
                    artist = (track.Artist.Name.strip().lower() if track.Artist and track.Artist.Name else "")
                    title = (track.Title or "").strip().lower()
                    if artist and title:
                        metadata_groups[(artist, title)].append(track)
                candidate_groups = [group for group in metadata_groups.values() if len(group) > 1]
                if candidate_groups:
                    click.echo("\nPotential duplicates by artist+title (diagnostic):")
                    for group in sorted(candidate_groups, key=len, reverse=True)[:15]:
                        sample = group[0]
                        artist = sample.Artist.Name if sample.Artist else "?"
                        title = sample.Title or "?"
                        ids = ", ".join(str(t.ID) for t in group[:8])
                        suffix = " ..." if len(group) > 8 else ""
                        click.echo(f"  {len(group):2d}x  {artist} - {title}  [IDs: {ids}{suffix}]")
            return

        duplicate_ids = {
            t.ID
            for group in duplicate_groups.values()
            for t in group
        }

        ref_tables = _content_reference_tables()
        ref_counts = defaultdict(int)
        for table_cls in ref_tables:
            rows = db.session.query(table_cls).filter(table_cls.ContentID.in_(duplicate_ids)).all()
            for row in rows:
                ref_counts[row.ContentID] += 1

        keep_by_duplicate_id = {}
        groups_summary = []
        for path, group_tracks in duplicate_groups.items():
            keeper = _choose_keeper(group_tracks, ref_counts)
            duplicates = [t for t in group_tracks if t.ID != keeper.ID]
            for duplicate in duplicates:
                keep_by_duplicate_id[duplicate.ID] = keeper
            groups_summary.append((path, keeper, duplicates))

        if not keep_by_duplicate_id:
            click.echo("No duplicates found.")
            return

        updates_by_table = defaultdict(int)
        remapped_preview = []
        rows_to_update = []

        for table_cls in ref_tables:
            rows = db.session.query(table_cls).filter(table_cls.ContentID.in_(keep_by_duplicate_id.keys())).all()
            for row in rows:
                keeper = keep_by_duplicate_id.get(row.ContentID)
                if not keeper or row.ContentID == keeper.ID:
                    continue

                updates_by_table[table_cls.__name__] += 1
                rows_to_update.append((table_cls, row, keeper))
                if verbose and len(remapped_preview) < 30:
                    remapped_preview.append(f"{table_cls.__name__}: {row.ContentID} -> {keeper.ID}")

        deleted = len(keep_by_duplicate_id)

        click.echo("\nDuplicate cleanup summary:")
        click.echo(f"  Duplicate groups: {len(groups_summary)}")
        click.echo(f"  Tracks to remove: {len(keep_by_duplicate_id)}")
        click.echo(f"  Reference updates: {sum(updates_by_table.values())}")

        if verbose:
            click.echo("\nDuplicate groups:")
            for path, keeper, duplicates in groups_summary:
                if str(path).startswith("tentative::"):
                    click.echo(f"  Tentative key: {str(path).replace('tentative::', '')}")
                else:
                    click.echo(f"  Path: {path}")
                click.echo(f"    Keep: {keeper.ID}  {(keeper.Artist.Name if keeper.Artist else '?')} - {keeper.Title or '?'}")
                for duplicate in duplicates:
                    click.echo(f"    Drop: {duplicate.ID}  {(duplicate.Artist.Name if duplicate.Artist else '?')} - {duplicate.Title or '?'}")

            if updates_by_table:
                click.echo("\nReference updates by table:")
                for table_name, count in sorted(updates_by_table.items()):
                    click.echo(f"  {table_name:<26s} {count}")
            if remapped_preview:
                click.echo("\nReference remap preview:")
                for line in remapped_preview:
                    click.echo(f"  {line}")

        if dry_run:
            click.echo("\nNo changes written (dry run).")
            return

        click.echo()
        if not click.confirm(f"Remove {len(keep_by_duplicate_id)} duplicate tracks and apply reference updates?"):
            db.session.rollback()
            click.echo("Aborted.")
            return

        for table_cls, row, keeper in rows_to_update:
            row.ContentID = keeper.ID
            if hasattr(table_cls, "ContentUUID"):
                row.ContentUUID = keeper.UUID if getattr(keeper, "UUID", None) else None

        removed = 0
        for duplicate_id in keep_by_duplicate_id:
            duplicate_track = db.session.get(DjmdContent, duplicate_id)
            if duplicate_track is not None:
                db.session.delete(duplicate_track)
                removed += 1

        try:
            backup = safe_commit(db, "collection_dedupe")
        except Exception:
            db.session.rollback()
            raise

        click.echo(click.style(f"\n✓ Removed {removed} duplicate tracks.", fg="green"))
        click.echo(f"  Backup at: {backup}")
    finally:
        db.close()


@cli.command("enrich")
@click.option("--dry-run", is_flag=True, help="Preview changes without writing.")
@click.option("--force", is_flag=True, help="Update all tracks, not just incomplete ones.")
@click.option("--verbose", "-v", is_flag=True, help="Show per-track details.")
@click.option(
    "--playlist", "-p", "playlist_name", default=None,
    help="Restrict enrichment to tracks in this playlist (fuzzy match).",
)
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
def enrich(dry_run, force, verbose, playlist_name, spotify_id, spotify_secret, lastfm_key):
    """Fix and enrich track metadata (title, artist, album, year, label) from Spotify/Last.fm."""
    from .enrich import enrich_tracks, print_enrich_summary

    click.echo("Opening rekordbox database...")
    db = get_database()

    playlist_content_ids = None
    if playlist_name:
        all_playlists = db.get_playlist().all()
        matches = [p for p in all_playlists if playlist_name.lower() in (p.Name or "").lower()]
        if not matches:
            available = ", ".join(p.Name for p in all_playlists if p.Name)
            click.echo(click.style(f"✗ Playlist '{playlist_name}' not found.", fg="red"))
            click.echo(f"Available: {available}")
            db.close()
            return
        playlist_obj = matches[0]
        songs = db.get_playlist_songs(PlaylistID=playlist_obj.ID).all()
        playlist_content_ids = {s.ContentID for s in songs}
        click.echo(f"Filtering to playlist '{playlist_obj.Name}' ({len(playlist_content_ids)} tracks).")

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
        playlist_content_ids=playlist_content_ids,
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
