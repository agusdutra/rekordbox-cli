"""Beatport scraper for granular electronic music genre/subgenre lookups."""

import time
import re

import requests
from bs4 import BeautifulSoup


class BeatportClient:
    """Scraper for Beatport search results to get genre/subgenre info."""

    BASE_URL = "https://www.beatport.com"
    SEARCH_URL = "https://www.beatport.com/search"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._last_request = 0.0
        self._rate_limit = 2.0  # Be polite: 1 req per 2 sec
        self._cache: dict[str, str | None] = {}

    def _throttle(self):
        """Rate limit requests."""
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_request = time.time()

    def search_track(self, artist: str, title: str) -> dict | None:
        """Search Beatport for a track and return genre info.
        
        Returns dict with keys: genre, subgenre, bpm, key, title, artists
        """
        cache_key = f"{artist}|{title}".lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        clean_title = title.split("(")[0].split("[")[0].strip()
        query = f"{artist} {clean_title}".strip()

        self._throttle()
        try:
            resp = self._session.get(
                self.SEARCH_URL,
                params={"q": query, "type": "tracks"},
                timeout=15,
            )
            if resp.status_code != 200:
                self._cache[cache_key] = None
                return None

            result = self._parse_search_results(resp.text, artist, clean_title)
            self._cache[cache_key] = result
            return result

        except requests.RequestException:
            self._cache[cache_key] = None
            return None

    def _parse_search_results(self, html: str, artist: str, title: str) -> dict | None:
        """Parse Beatport search results HTML for track info."""
        soup = BeautifulSoup(html, "html.parser")

        # Beatport uses JSON-LD or data attributes for track info
        # Try to find track cards with genre info
        tracks = soup.select("[data-testid='track-row'], .track-grid-content, .bucket-item")

        if not tracks:
            # Try alternative: look for script with JSON data
            return self._parse_json_data(soup, artist, title)

        for track in tracks[:5]:  # Check first 5 results
            track_info = self._extract_track_info(track)
            if track_info:
                return track_info

        return self._parse_json_data(soup, artist, title)

    def _extract_track_info(self, element) -> dict | None:
        """Extract genre info from a track element."""
        genre_el = element.select_one(".genre, [data-testid='genre'], .buk-track-genre")
        if not genre_el:
            return None

        genre_text = genre_el.get_text(strip=True)
        parts = genre_text.split("/") if "/" in genre_text else [genre_text]

        return {
            "genre": parts[0].strip() if parts else None,
            "subgenre": parts[1].strip() if len(parts) > 1 else None,
        }

    def _parse_json_data(self, soup: BeautifulSoup, artist: str, title: str) -> dict | None:
        """Try to extract track data from embedded JSON (Next.js data)."""
        # Look for Next.js __NEXT_DATA__ or similar embedded JSON
        scripts = soup.find_all("script", {"id": "__NEXT_DATA__"})
        if not scripts:
            scripts = soup.find_all("script", type="application/json")

        for script in scripts:
            try:
                import json
                data = json.loads(script.string or "")
                return self._find_track_in_json(data, artist, title)
            except (json.JSONDecodeError, TypeError):
                continue

        # Fallback: regex for genre in page text
        return self._regex_fallback(soup)

    def _find_track_in_json(self, data: dict, artist: str, title: str) -> dict | None:
        """Recursively find track info in JSON data."""
        if isinstance(data, dict):
            # Look for genre/subgenre keys
            if "genre" in data and "name" in data.get("genre", {}):
                genre_name = data["genre"]["name"]
                sub_genre = data.get("sub_genre", {})
                sub_name = sub_genre.get("name") if isinstance(sub_genre, dict) else None
                return {
                    "genre": genre_name,
                    "subgenre": sub_name,
                }
            # Look for tracks array
            for key in ("tracks", "results", "items", "data"):
                if key in data and isinstance(data[key], list):
                    for item in data[key][:5]:
                        result = self._find_track_in_json(item, artist, title)
                        if result:
                            return result
            # Recurse into props
            if "props" in data:
                return self._find_track_in_json(data["props"], artist, title)
            if "pageProps" in data:
                return self._find_track_in_json(data["pageProps"], artist, title)

        return None

    def _regex_fallback(self, soup: BeautifulSoup) -> dict | None:
        """Last resort: try to find genre mentions in page text."""
        text = soup.get_text()
        # Common Beatport genres
        bp_genres = [
            "Afro House", "Deep House", "Tech House", "Melodic House & Techno",
            "Techno (Peak Time / Driving)", "Techno (Raw / Deep / Hypnotic)",
            "Minimal / Deep Tech", "Progressive House", "House",
            "Electro House", "Bass House", "Future House",
            "Trance", "Psy-Trance", "Hard Techno", "Hard Dance / Hardcore",
            "Drum & Bass", "Dubstep", "UK Garage / Bassline",
            "Organic House / Downtempo", "Electronica", "Nu Disco / Disco",
            "Indie Dance", "Funky / Groove / Jackin' House",
            "Mainstage", "Dance / Electro Pop", "Breaks / Breakbeat / UK Bass",
        ]
        for genre in bp_genres:
            if genre.lower() in text.lower():
                parts = genre.split(" / ")
                return {
                    "genre": parts[0],
                    "subgenre": parts[1] if len(parts) > 1 else None,
                }
        return None

    def get_genre(self, artist: str, title: str) -> str | None:
        """Get the most specific genre/subgenre for a track."""
        result = self.search_track(artist, title)
        if not result:
            return None

        # Prefer subgenre (more specific)
        subgenre = result.get("subgenre")
        genre = result.get("genre")

        if subgenre:
            return _normalize_beatport_genre(subgenre)
        if genre:
            return _normalize_beatport_genre(genre)
        return None


# Beatport genre normalization
BEATPORT_GENRE_MAP = {
    "afro house": "Afro House",
    "deep house": "Deep House",
    "tech house": "Tech House",
    "melodic house & techno": "Melodic House",
    "melodic techno": "Melodic Techno",
    "techno (peak time / driving)": "Peak Time Techno",
    "techno (raw / deep / hypnotic)": "Deep Techno",
    "minimal / deep tech": "Minimal / Deep Tech",
    "progressive house": "Progressive House",
    "house": "House",
    "electro house": "Electro House",
    "bass house": "Bass House",
    "future house": "Future House",
    "trance": "Trance",
    "psy-trance": "Psytrance",
    "hard techno": "Hard Techno",
    "hard dance / hardcore": "Hardcore",
    "drum & bass": "Drum & Bass",
    "dubstep": "Dubstep",
    "uk garage / bassline": "UK Garage",
    "organic house / downtempo": "Downtempo",
    "electronica": "Electronica",
    "nu disco / disco": "Nu Disco",
    "indie dance": "Indie Dance",
    "funky / groove / jackin' house": "Jackin House",
    "mainstage": "Mainstage",
    "dance / electro pop": "Dance Pop",
    "breaks / breakbeat / uk bass": "Breakbeat",
    "techno": "Techno",
    "dance": "Dance",
}


def _normalize_beatport_genre(genre: str) -> str:
    """Normalize Beatport genre to standard name."""
    lower = genre.lower().strip()
    if lower in BEATPORT_GENRE_MAP:
        return BEATPORT_GENRE_MAP[lower]
    return genre.strip()
