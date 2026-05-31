"""Write genre tags to audio files (ID3 for MP3, INFO for WAV)."""

import os

import click
from mutagen.id3 import ID3, TCON, ID3NoHeaderError
from mutagen.wave import WAVE


def write_genre_to_file(filepath: str, genre: str) -> bool:
    """Write genre tag to an audio file. Returns True on success."""
    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == ".mp3":
            return _write_mp3_genre(filepath, genre)
        elif ext == ".wav":
            return _write_wav_genre(filepath, genre)
    except Exception:
        return False
    return False


def _write_mp3_genre(filepath: str, genre: str) -> bool:
    """Write genre to MP3 via ID3 TCON frame."""
    try:
        tags = ID3(filepath)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TCON")
    tags.add(TCON(encoding=3, text=[genre]))
    tags.save(filepath)
    return True


def _write_wav_genre(filepath: str, genre: str) -> bool:
    """Write genre to WAV via ID3 tags."""
    audio = WAVE(filepath)
    if audio.tags is None:
        audio.add_tags()
    audio.tags.delall("TCON")
    audio.tags.add(TCON(encoding=3, text=[genre]))
    audio.save()
    return True


def sync_genres_to_files(db, dry_run: bool = False, verbose: bool = False) -> dict:
    """Write genre from rekordbox DB into the actual audio file tags.

    Args:
        db: Rekordbox6Database instance.
        dry_run: If True, don't write to files.
        verbose: If True, print per-file details.

    Returns:
        dict with keys: total, written, skipped, failed
    """
    tracks = db.get_content().all()

    # Only local files with a genre assigned
    targets = [
        t for t in tracks
        if t.Genre and t.FolderPath
        and not t.FolderPath.startswith("spotify:")
        and not t.FolderPath.startswith("soundcloud:")
        and os.path.exists(t.FolderPath)
    ]

    written = 0
    skipped = 0
    failed = 0

    for t in targets:
        genre_name = t.Genre.Name
        filepath = t.FolderPath
        ext = os.path.splitext(filepath)[1].lower()

        if ext not in (".mp3", ".wav"):
            skipped += 1
            if verbose:
                click.echo(click.style(f"  ⊘ Unsupported format: {filepath}", fg="yellow"))
            continue

        if not dry_run:
            success = write_genre_to_file(filepath, genre_name)
            if success:
                written += 1
                if verbose:
                    click.echo(click.style(f"  ✓ {genre_name} → {os.path.basename(filepath)}", fg="green"))
            else:
                failed += 1
                if verbose:
                    click.echo(click.style(f"  ✗ Failed: {filepath}", fg="red"))
        else:
            written += 1
            if verbose:
                click.echo(f"  {genre_name} → {os.path.basename(filepath)}")

    return {
        "total": len(targets),
        "written": written,
        "skipped": skipped,
        "failed": failed,
    }
