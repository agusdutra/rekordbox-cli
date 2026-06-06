"""Apple Music / iTunes Search API for track-level genre lookups.

Uses the free iTunes Search API (no API key needed).
Returns track-level genres (not just artist-level like Spotify).
Excellent for Latin music classification (Urbano Latino, Música Tropical, etc.)
"""

import time

import requests


class AppleMusicClient:
    """Client for iTunes Search API with rate limiting."""

    SEARCH_URL = "https://itunes.apple.com/search"

    def __init__(self):
        self._last_request = 0.0
        self._rate_limit = 0.35  # ~3 req/sec (Apple limit: ~20/min)
        self._cache: dict[str, dict | None] = {}

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_request = time.time()

    def search_track(self, artist: str, title: str) -> dict | None:
        """Search iTunes for a track. Returns the first matching result."""
        cache_key = f"{artist}|{title}".lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        clean_title = title.split("(")[0].split("[")[0].strip()
        query = f"{artist} {clean_title}".strip()

        self._throttle()
        try:
            resp = requests.get(
                self.SEARCH_URL,
                params={
                    "term": query,
                    "media": "music",
                    "entity": "song",
                    "limit": 5,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                # Find best match (prefer exact artist match)
                best = self._best_match(results, artist, clean_title)
                self._cache[cache_key] = best
                return best
            elif resp.status_code == 429:
                time.sleep(10)
                self._cache[cache_key] = None
                return None
        except requests.RequestException:
            pass

        self._cache[cache_key] = None
        return None

    def _best_match(self, results: list[dict], artist: str, title: str) -> dict | None:
        """Find the best matching result from iTunes search."""
        if not results:
            return None

        artist_lower = artist.lower()
        title_lower = title.lower()

        # Prefer exact artist + title match
        for r in results:
            r_artist = r.get("artistName", "").lower()
            r_title = r.get("trackName", "").lower()
            if artist_lower in r_artist and title_lower in r_title:
                return r

        # Prefer artist match
        for r in results:
            r_artist = r.get("artistName", "").lower()
            if artist_lower in r_artist or r_artist in artist_lower:
                return r

        # Fall back to first result
        return results[0]

    def get_genre(self, artist: str, title: str) -> str | None:
        """Get the genre for a track from Apple Music/iTunes."""
        result = self.search_track(artist, title)
        if not result:
            return None

        genre = result.get("primaryGenreName")
        if genre:
            return _normalize_apple_genre(genre)
        return None

    def get_track_info(self, artist: str, title: str) -> dict | None:
        """Get full track info including genre, BPM hint, year, etc."""
        result = self.search_track(artist, title)
        if not result:
            return None

        return {
            "genre": _normalize_apple_genre(result.get("primaryGenreName", "")),
            "artist": result.get("artistName"),
            "title": result.get("trackName"),
            "album": result.get("collectionName"),
            "year": result.get("releaseDate", "")[:4] if result.get("releaseDate") else None,
            "duration_ms": result.get("trackTimeMillis"),
        }


# Apple Music genre normalization
APPLE_GENRE_MAP = {
    # Electronic / Dance
    "house": "House",
    "dance": "Dance",
    "electronic": "Electronic",
    "electronica": "Electronica",
    "techno": "Techno",
    "trance": "Trance",
    "ambient": "Ambient",
    "downtempo": "Downtempo",
    # Latin
    "urbano latino": "Reggaeton",
    "pop latino": "Latin Pop",
    "música tropical": "Tropical",
    "reggaetón": "Reggaeton",
    "reggaeton": "Reggaeton",
    "salsa y tropical": "Salsa",
    "salsa": "Salsa",
    "bachata": "Bachata",
    "merengue": "Merengue",
    "cumbia": "Cumbia",
    "regional mexicano": "Regional Mexicano",
    "latin": "Latin Pop",
    "musica mexicana": "Regional Mexicano",
    "música mexicana": "Regional Mexicano",
    # Rock
    "rock": "Rock",
    "rock en español": "Rock Nacional",
    "alternative": "Alternative",
    "indie rock": "Indie Rock",
    "hard rock": "Hard Rock",
    "metal": "Metal",
    "punk": "Punk",
    # Pop
    "pop": "Pop",
    "dance pop": "Dance Pop",
    "k-pop": "K-Pop",
    "j-pop": "J-Pop",
    # Hip Hop
    "hip-hop/rap": "Hip Hop",
    "hip hop/rap": "Hip Hop",
    "hip-hop": "Hip Hop",
    "rap": "Hip Hop",
    # R&B / Soul
    "r&b/soul": "R&B",
    "r&b": "R&B",
    "soul": "Soul",
    "funk": "Funk",
    # Reggae / Caribbean
    "reggae": "Reggae",
    "dancehall": "Dancehall",
    # Other
    "disco": "Disco",
    "jazz": "Jazz",
    "blues": "Blues",
    "country": "Country",
    "folk": "Folk",
    "classical": "Classical",
    "singer/songwriter": "Singer-Songwriter",
    "worldwide": "World",
    "fitness & workout": "Dance",
}


def _normalize_apple_genre(genre: str) -> str:
    """Normalize an Apple Music genre to standard name."""
    if not genre:
        return ""
    lower = genre.lower().strip()
    if lower in APPLE_GENRE_MAP:
        return APPLE_GENRE_MAP[lower]
    return genre.strip()
