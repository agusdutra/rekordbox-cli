"""Spotify API integration for genre lookups."""

import time
from functools import lru_cache

import requests


class SpotifyClient:
    """Client for Spotify Web API using Client Credentials flow."""

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE = "https://api.spotify.com/v1"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None
        self._token_expiry = 0
        self._rate_limit = 0.05  # ~20 req/sec
        self._last_request = 0.0
        self._artist_cache: dict[str, list[str]] = {}

    def _get_token(self) -> str:
        """Get or refresh access token."""
        if self._token and time.time() < self._token_expiry:
            return self._token

        resp = requests.post(
            self.TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data["expires_in"] - 60
        return self._token

    def _request(self, endpoint: str, params: dict = None) -> dict | None:
        """Make a rate-limited API request."""
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)

        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            self._last_request = time.time()
            resp = requests.get(
                f"{self.API_BASE}/{endpoint}",
                headers=headers,
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                return self._request(endpoint, params)
        except requests.RequestException:
            pass
        return None

    def search_track(self, artist: str, title: str) -> dict | None:
        """Search for a track and return the first result."""
        # Clean up title for better search results
        clean_title = title.split("(")[0].split("[")[0].strip()
        query = f"track:{clean_title}"
        if artist:
            query += f" artist:{artist}"

        data = self._request("search", {"q": query, "type": "track", "limit": 1})
        if data and data.get("tracks", {}).get("items"):
            return data["tracks"]["items"][0]
        return None

    def get_artist_genres(self, artist_id: str) -> list[str]:
        """Get genres for an artist by ID (cached)."""
        if artist_id in self._artist_cache:
            return self._artist_cache[artist_id]

        data = self._request(f"artists/{artist_id}")
        genres = data.get("genres", []) if data else []
        self._artist_cache[artist_id] = genres
        return genres

    def get_genre(self, artist: str, title: str) -> str | None:
        """Get the best genre for a track via Spotify artist genres."""
        track = self.search_track(artist, title)
        if not track:
            return None

        # Get genres from the primary artist
        artists = track.get("artists", [])
        for art in artists:
            genres = self.get_artist_genres(art["id"])
            if genres:
                return _pick_best_genre(genres)

        return None


# Genre normalization for Spotify's genre strings
SPOTIFY_GENRE_MAP = {
    "reggaeton": "Reggaeton",
    "latin pop": "Latin Pop",
    "pop": "Pop",
    "dance pop": "Dance Pop",
    "rock": "Rock",
    "rock en espanol": "Rock Nacional",
    "argentine rock": "Rock Nacional",
    "cumbia": "Cumbia",
    "cumbia villera": "Cumbia",
    "cuarteto": "Cuarteto",
    "house": "House",
    "deep house": "Deep House",
    "tech house": "Tech House",
    "edm": "EDM",
    "electronic": "Electronic",
    "disco": "Disco",
    "nu disco": "Nu Disco",
    "hip hop": "Hip Hop",
    "rap": "Hip Hop",
    "r&b": "R&B",
    "dancehall": "Dancehall",
    "salsa": "Salsa",
    "bachata": "Bachata",
    "merengue": "Merengue",
    "funk": "Funk",
    "soul": "Soul",
    "jazz": "Jazz",
    "blues": "Blues",
    "country": "Country",
    "folk": "Folk",
    "metal": "Metal",
    "punk": "Punk",
    "classical": "Classical",
    "ambient": "Ambient",
    "trance": "Trance",
    "drum and bass": "Drum & Bass",
    "tropical": "Tropical",
    "afrobeat": "Afrobeat",
}


def _pick_best_genre(genres: list[str]) -> str | None:
    """Pick the most specific/useful genre from Spotify's genre list."""
    if not genres:
        return None

    # Try exact match in our map first
    for g in genres:
        lower = g.lower()
        if lower in SPOTIFY_GENRE_MAP:
            return SPOTIFY_GENRE_MAP[lower]

    # Try partial match
    for g in genres:
        lower = g.lower()
        for key, value in SPOTIFY_GENRE_MAP.items():
            if key in lower:
                return value

    # Fall back to title-casing the first genre
    return genres[0].title()
