"""Discogs API integration for granular subgenre/style lookups."""

import time

import requests


class DiscogsClient:
    """Client for Discogs API with rate limiting (60 req/min)."""

    API_BASE = "https://api.discogs.com"

    def __init__(self, token: str):
        self.token = token
        self._last_request = 0.0
        self._rate_limit = 1.0  # 1 req/sec (Discogs limit: 60/min)
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Discogs token={token}",
            "User-Agent": "RekordboxCLI/1.0",
        })

    def _request(self, endpoint: str, params: dict = None) -> dict | None:
        """Make a rate-limited API request."""
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)

        try:
            self._last_request = time.time()
            resp = self._session.get(
                f"{self.API_BASE}/{endpoint}",
                params=params,
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                time.sleep(retry_after)
                return self._request(endpoint, params)
        except requests.RequestException:
            pass
        return None

    def search_track(self, artist: str, title: str) -> dict | None:
        """Search for a release matching artist + title."""
        # Clean title
        clean_title = title.split("(")[0].split("[")[0].strip()
        query = f"{artist} {clean_title}".strip()

        data = self._request("database/search", {
            "q": query,
            "type": "release",
            "per_page": 5,
        })
        if not data or not data.get("results"):
            return None

        # Return best match (first result with styles)
        for result in data["results"]:
            if result.get("style"):
                return result
        return data["results"][0]

    def get_release_styles(self, release_id: int) -> list[str]:
        """Get detailed styles from a specific release."""
        data = self._request(f"releases/{release_id}")
        if not data:
            return []
        return data.get("styles", [])

    def get_genre(self, artist: str, title: str) -> tuple[str | None, list[str]]:
        """Get genre and styles for a track.
        
        Returns (primary_genre, [styles]) where styles are the granular subgenres.
        e.g. ("Electronic", ["Deep House", "Tech House"])
        or   ("Latin", ["Cumbia Villera", "Cumbia"])
        """
        result = self.search_track(artist, title)
        if not result:
            return None, []

        genres = result.get("genre", [])
        styles = result.get("style", [])

        primary_genre = genres[0] if genres else None
        return primary_genre, styles

    def get_subgenre(self, artist: str, title: str) -> str | None:
        """Get the most specific subgenre/style for a track."""
        primary, styles = self.get_genre(artist, title)

        if styles:
            # Prefer the most specific style (usually the first one is most relevant)
            return _normalize_discogs_style(styles[0])
        if primary:
            return _normalize_discogs_style(primary)
        return None


# Map Discogs styles to normalized genre names
DISCOGS_STYLE_MAP = {
    # Electronic
    "deep house": "Deep House",
    "tech house": "Tech House",
    "minimal": "Minimal / Deep Tech",
    "progressive house": "Progressive House",
    "electro house": "Electro House",
    "house": "House",
    "techno": "Techno",
    "minimal techno": "Minimal Techno",
    "acid house": "Acid House",
    "chicago house": "Chicago House",
    "detroit techno": "Detroit Techno",
    "trance": "Trance",
    "progressive trance": "Progressive Trance",
    "disco": "Disco",
    "nu-disco": "Nu Disco",
    "italo-disco": "Italo Disco",
    "euro disco": "Euro Disco",
    "synth-pop": "Synth Pop",
    "electro": "Electro",
    "downtempo": "Downtempo",
    "ambient": "Ambient",
    "drum n bass": "Drum & Bass",
    "dubstep": "Dubstep",
    "garage house": "UK Garage",
    "uk garage": "UK Garage",
    "breakbeat": "Breakbeat",
    "trip hop": "Trip Hop",
    "industrial": "Industrial",
    "ebm": "EBM",
    # Latin
    "cumbia": "Cumbia",
    "cumbia villera": "Cumbia Villera",
    "cumbia colombiana": "Cumbia Colombiana",
    "cumbia digital": "Cumbia Digital",
    "reggaeton": "Reggaeton",
    "salsa": "Salsa",
    "bachata": "Bachata",
    "merengue": "Merengue",
    "vallenato": "Vallenato",
    "bossa nova": "Bossa Nova",
    "samba": "Samba",
    "tango": "Tango",
    "latin jazz": "Latin Jazz",
    "tropical": "Tropical",
    "guaracha": "Guaracha",
    "champeta": "Champeta",
    # Rock
    "rock & roll": "Rock & Roll",
    "classic rock": "Classic Rock",
    "hard rock": "Hard Rock",
    "punk": "Punk",
    "post-punk": "Post-Punk",
    "new wave": "New Wave",
    "indie rock": "Indie Rock",
    "alternative rock": "Alternative Rock",
    "grunge": "Grunge",
    "psychedelic rock": "Psychedelic Rock",
    "progressive rock": "Progressive Rock",
    "garage rock": "Garage Rock",
    "pop rock": "Pop Rock",
    # Pop / Dance
    "pop": "Pop",
    "dance-pop": "Dance Pop",
    "europop": "Europop",
    "bubblegum": "Pop",
    "synthwave": "Synthwave",
    # Hip Hop
    "hip hop": "Hip Hop",
    "trap": "Trap",
    "boom bap": "Boom Bap",
    "gangsta": "Gangsta Rap",
    "conscious": "Conscious Hip Hop",
    "g-funk": "G-Funk",
    "crunk": "Crunk",
    # Funk / Soul
    "funk": "Funk",
    "soul": "Soul",
    "neo soul": "Neo Soul",
    "disco": "Disco",
    "boogie": "Boogie",
    "p.funk": "P-Funk",
    # Reggae / Caribbean
    "reggae": "Reggae",
    "dancehall": "Dancehall",
    "dub": "Dub",
    "ska": "Ska",
    "soca": "Soca",
    # Argentine
    "cuarteto": "Cuarteto",
    "rock nacional": "Rock Nacional",
    "tango electrónico": "Tango Electrónico",
}


def _normalize_discogs_style(style: str) -> str:
    """Normalize a Discogs style to a standard genre name."""
    lower = style.lower().strip()
    if lower in DISCOGS_STYLE_MAP:
        return DISCOGS_STYLE_MAP[lower]
    return style.strip()
