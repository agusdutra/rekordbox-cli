"""Playlist sorting helpers."""

import re
from os.path import normpath
from pathlib import Path
from urllib.parse import unquote

import click

# Serpentine walk of the Camelot wheel: every adjacent pair is a compatible
# DJ transition, alternating relative major/minor moves (same number, A<->B)
# with perfect-fifth moves around the circle of fifths (same letter, +/-1 number).
HARMONIC_WHEEL = [
    "8A", "8B",
    "9B", "9A",
    "10A", "10B",
    "11B", "11A",
    "12A", "12B",
    "1B", "1A",
    "2A", "2B",
    "3B", "3A",
    "4A", "4B",
    "5B", "5A",
    "6A", "6B",
    "7B", "7A",
]
CAMELOT_RANK = {code: idx for idx, code in enumerate(HARMONIC_WHEEL)}
_CAMELOT_MINOR = {
    "a": "8A",
    "a#": "3A",
    "b": "10A",
    "c": "5A",
    "c#": "12A",
    "d": "7A",
    "d#": "2A",
    "e": "9A",
    "f": "4A",
    "f#": "11A",
    "g": "6A",
    "g#": "1A",
}
_CAMELOT_MAJOR = {
    "c": "8B",
    "c#": "3B",
    "d": "10B",
    "d#": "5B",
    "e": "12B",
    "f": "7B",
    "f#": "2B",
    "g": "9B",
    "g#": "4B",
    "a": "11B",
    "a#": "6B",
    "b": "1B",
}
_CAMELOT_ALIASES = {
    "db": "c#",
    "eb": "d#",
    "gb": "f#",
    "ab": "g#",
    "bb": "a#",
    "cb": "b",
    "fb": "e",
    "e#": "f",
    "b#": "c",
}

GENRE_PRIORITY_GROUPS = [
    (
        0,
        (
            "children's music",
            "educational",
            "instrumental",
            "loop samples",
            "piano",
            "soundtrack",
            "singer-songwriter",
            "ballad",
            "balada",
            "bolero",
            "lounge",
            "folk",
            "folk rock",
            "blues",
            "blues rock",
        ),
    ),
    (
        1,
        (
            "oldies",
            "classic rock",
            "rock & roll",
            "rockabilly",
            "rock",
            "rock nacional",
            "rock argentino",
            "rock y alternativo",
            "alternative",
            "alternative folk",
            "alternative rock",
            "alt-pop",
            "pop",
            "pop español",
            "pop español 90s",
            "pop rock",
            "pop/rock",
            "pop punk",
            "pop rap",
            "c-pop",
            "german pop",
            "europop",
            "dutch",
            "europe",
            "r&b",
            "rhythm & blues",
            "contemporary r&b",
            "soul",
            "neo soul",
            "reggae",
            "ska",
            "world",
            "spanish",
            "rihanna",
        ),
    ),
    (
        2,
        (
            "latin",
            "latin pop",
            "contemporary latin",
            "pop latino",
            "latin rap",
            "latin remix",
            "brazilian",
            "sertanejo",
            "flamenco",
            "tropical",
            "caribbean",
            "nacional",
        ),
    ),
    (
        3,
        (
            "cumbia",
            "cumbia pop",
            "cumbia tropical",
            "cumbia villera",
            "cuarteto",
            "salsa",
            "merengue",
            "bachata",
            "reggaeton",
            "party",
            "dance",
            "dance pop",
            "dancehall",
            "modern dancehall",
            "disco",
            "nu disco",
            "remixes latinos",
            "pop remix",
            "dance & edm",
        ),
    ),
    (
        4,
        (
            "electronic",
            "electronica",
            "electro",
            "edm",
            "euro house",
            "house",
            "afro house",
            "tech house",
            "big beat",
            "premiere",
            "premiere recordeep",
        ),
    ),
    (
        5,
        (
            "techno",
            "trance",
            "hard house",
            "ebm",
            "hardcore",
            "hardcore hip-hop",
            "hip hop",
            "hip-house",
            "makina",
            "trap",
            "turreo",
        ),
    ),
]


def parse_sort_priority(sort_by: str) -> tuple[list[str], bool]:
    """Parse and validate playlist sort priorities."""
    priorities = [p.strip().lower() for p in sort_by.split(",") if p.strip()]
    if len(priorities) != 3 or set(priorities) != {"genre", "key", "bpm"}:
        raise click.ClickException(
            "Invalid --by value. Use all fields once, e.g. 'genre,key,bpm', 'bpm,key,genre', or 'key,bpm,genre'."
        )
    return priorities, priorities == ["key", "bpm", "genre"]


def key_to_camelot(key_name: str) -> str | None:
    """Convert a rekordbox key name to a Camelot code."""
    if not key_name:
        return None

    normalized = key_name.strip().lower().replace("♭", "b").replace("♯", "#")
    normalized = normalized.replace("major", "").replace("minor", "m").replace(" ", "")
    camelot_code = normalized.upper()
    if camelot_code in CAMELOT_RANK:
        return camelot_code

    match = re.fullmatch(r"([a-g])([#b]?)(m?)", normalized)
    if not match:
        return None

    note = f"{match.group(1)}{match.group(2)}"
    note = _CAMELOT_ALIASES.get(note, note)
    is_minor = bool(match.group(3))
    return (_CAMELOT_MINOR if is_minor else _CAMELOT_MAJOR).get(note)


def key_sort_rank(key_name: str) -> tuple:
    """Sort keys using circle-of-fifths progression."""
    camelot = key_to_camelot(key_name)
    if camelot:
        return (0, CAMELOT_RANK[camelot], camelot)
    return (1, (key_name or "").lower())


def build_genre_priority_map(genres) -> dict[str, int]:
    """Build an energy-based rank map for genres present in the database."""
    genre_names = sorted({(genre.Name or "").strip() for genre in genres if genre.Name and genre.Name.strip()})
    if not genre_names:
        return {}

    priority_map: dict[str, int] = {}
    matched = set()

    for rank, patterns in GENRE_PRIORITY_GROUPS:
        for genre_name in genre_names:
            lowered = genre_name.lower()
            if lowered in matched:
                continue
            if any(re.search(rf"(?<!\w){re.escape(pattern)}(?!\w)", lowered) for pattern in patterns):
                priority_map[lowered] = rank
                matched.add(lowered)

    fallback_rank = max(rank for rank, _ in GENRE_PRIORITY_GROUPS) + 1
    for offset, genre_name in enumerate(name for name in genre_names if name.lower() not in matched):
        priority_map[genre_name.lower()] = fallback_rank + offset

    return priority_map


def playlist_sort_key(
    fields: dict,
    priorities: list[str],
    genre_priority_map: dict[str, int],
    include_tiebreakers: bool = True,
):
    """Build tuple sort key from selected priority order."""
    parts = []
    for priority in priorities:
        if priority == "genre":
            genre = fields["genre"].lower()
            parts.append((0, genre_priority_map.get(genre, 999), genre) if genre else (1, 999, ""))
        elif priority == "key":
            parts.append(key_sort_rank(fields["key"]))
        elif priority == "bpm":
            bpm = fields["bpm"]
            parts.append((0, bpm) if bpm is not None else (1, float("inf")))

    if include_tiebreakers:
        parts.append(fields["artist"].lower())
        parts.append(fields["title"].lower())
    return tuple(parts)


def enforce_run_limits(sorted_rows: list[dict]) -> list[dict]:
    """Limit long genre and artist runs while keeping the base sort order stable."""
    if not sorted_rows:
        return sorted_rows

    remaining = list(sorted_rows)
    result = []
    last_genre = None
    genre_run = 0
    last_artist = None
    artist_run = 0

    while remaining:
        chosen_index = None
        for idx, row in enumerate(remaining):
            fields = row["fields"]
            genre = (fields["genre"] or "").lower()
            artist = (fields["artist"] or "").lower()
            next_genre_run = genre_run + 1 if genre and genre == last_genre else 1
            next_artist_run = artist_run + 1 if artist and artist == last_artist else 1
            if next_genre_run <= 3 and next_artist_run <= 2:
                chosen_index = idx
                break

        if chosen_index is None:
            chosen_index = 0

        row = remaining.pop(chosen_index)
        fields = row["fields"]
        genre = (fields["genre"] or "").lower()
        artist = (fields["artist"] or "").lower()

        if genre and genre == last_genre:
            genre_run += 1
        else:
            last_genre = genre
            genre_run = 1 if genre else 0

        if artist and artist == last_artist:
            artist_run += 1
        else:
            last_artist = artist
            artist_run = 1 if artist else 0

        result.append(row)

    return result
