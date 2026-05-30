"""rekordbox-cli: CLI entry point."""

import click

from .db import get_database, safe_commit
from .genre import print_summary, set_genres


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
def genre_set(dry_run, force):
    """Auto-tag genres on your collection based on playlists, artists, and keywords."""
    click.echo("Opening rekordbox database...")
    db = get_database()

    click.echo("Classifying tracks...")
    result = set_genres(db, dry_run=dry_run, force=force)
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
