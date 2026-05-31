"""Last.fm API integration for genre/tag lookups."""

import time
from functools import lru_cache

import click
import requests

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"

# Normalize Last.fm tags to clean genre names
TAG_NORMALIZATION = {
    "hip hop": "Hip Hop",
    "hip-hop": "Hip Hop",
    "rnb": "R&B",
    "r&b": "R&B",
    "rhythm and blues": "R&B",
    "electronica": "Electronic",
    "electro": "Electronic",
    "edm": "EDM",
    "deep house": "Deep House",
    "tech house": "Tech House",
    "indie rock": "Indie Rock",
    "alternative rock": "Alternative Rock",
    "alt rock": "Alternative Rock",
    "classic rock": "Classic Rock",
    "rock": "Rock",
    "pop": "Pop",
    "latin": "Latin Pop",
    "latin pop": "Latin Pop",
    "reggaeton": "Reggaeton",
    "reggae": "Reggae",
    "dancehall": "Dancehall",
    "cumbia": "Cumbia",
    "salsa": "Salsa",
    "bachata": "Bachata",
    "merengue": "Merengue",
    "disco": "Disco",
    "nu disco": "Nu Disco",
    "funk": "Funk",
    "soul": "Soul",
    "jazz": "Jazz",
    "house": "House",
    "techno": "Techno",
    "trance": "Trance",
    "drum and bass": "Drum & Bass",
    "dnb": "Drum & Bass",
    "dubstep": "Dubstep",
    "country": "Country",
    "folk": "Folk",
    "metal": "Metal",
    "punk": "Punk",
    "blues": "Blues",
    "classical": "Classical",
    "ambient": "Ambient",
    "dance": "Dance",
}

# Tags to ignore (too vague or not genres)
IGNORE_TAGS = {
    "seen live", "favorites", "favourite", "love", "awesome",
    "beautiful", "chill", "sexy", "party", "summer", "female vocalists",
    "male vocalists", "00s", "90s", "80s", "70s", "60s",
    "under 2000 listeners", "spotify",
}


def normalize_tag(tag: str) -> str | None:
    """Normalize a Last.fm tag to a standard genre name."""
    lower = tag.lower().strip()
    if lower in IGNORE_TAGS:
        return None
    if lower in TAG_NORMALIZATION:
        return TAG_NORMALIZATION[lower]
    # Title-case if not in our map (it's likely a valid genre)
    if len(lower) <= 3:
        return lower.upper()  # EDM, RNB, etc.
    return tag.strip().title()


class LastFmClient:
    """Client for Last.fm API with rate limiting."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._last_request = 0.0
        self._rate_limit = 0.2  # 5 requests/sec max

    def _request(self, method: str, **params) -> dict | None:
        """Make a rate-limited API request."""
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)

        params.update({
            "method": method,
            "api_key": self.api_key,
            "format": "json",
        })

        try:
            self._last_request = time.time()
            resp = requests.get(LASTFM_BASE, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except (requests.RequestException, ValueError):
            pass
        return None

    def get_track_tags(self, artist: str, title: str) -> list[str]:
        """Get top tags for a track."""
        data = self._request(
            "track.getTopTags", artist=artist, track=title
        )
        if not data or "toptags" not in data:
            return []

        tags = data["toptags"].get("tag", [])
        return [t["name"] for t in tags if int(t.get("count", 0)) >= 20]

    def get_artist_tags(self, artist: str) -> list[str]:
        """Get top tags for an artist (fallback when track has no tags)."""
        data = self._request("artist.getTopTags", artist=artist)
        if not data or "toptags" not in data:
            return []

        tags = data["toptags"].get("tag", [])
        return [t["name"] for t in tags if int(t.get("count", 0)) >= 30]

    def get_genre(self, artist: str, title: str) -> str | None:
        """Get the best genre for a track, trying track tags then artist tags."""
        # Try track-level tags first
        tags = self.get_track_tags(artist, title)
        genre = self._pick_best_genre(tags)
        if genre:
            return genre

        # Fall back to artist-level tags
        tags = self.get_artist_tags(artist)
        return self._pick_best_genre(tags)

    def _pick_best_genre(self, tags: list[str]) -> str | None:
        """Pick the most relevant genre from a list of tags."""
        for tag in tags:
            normalized = normalize_tag(tag)
            if normalized:
                return normalized
        return None
