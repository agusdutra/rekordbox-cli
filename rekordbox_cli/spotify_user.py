"""Spotify user authentication and playlist access via spotipy."""

import os
from pathlib import Path

import click
import spotipy
from spotipy.oauth2 import SpotifyOAuth

CACHE_PATH = Path(__file__).parent.parent / ".spotify_cache"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read playlist-modify-public playlist-modify-private"


def get_spotify_user_client(client_id: str = None, client_secret: str = None) -> spotipy.Spotify:
    """Get an authenticated Spotify client with user-level access.

    Uses cached token if available, otherwise triggers OAuth browser flow.
    """
    client_id = client_id or os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise click.ClickException(
            "Spotify credentials required. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env"
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_path=str(CACHE_PATH),
        open_browser=True,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def get_user_playlists(sp: spotipy.Spotify) -> list[dict]:
    """Get all playlists for the authenticated user."""
    playlists = []
    results = sp.current_user_playlists(limit=50)
    while results:
        playlists.extend(results["items"])
        results = sp.next(results) if results["next"] else None
    return playlists


def get_playlist_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    """Get all tracks from a playlist."""
    tracks = []
    results = sp.playlist_items(playlist_id, limit=100)
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("id"):  # skip local files & unavailable
                tracks.append(track)
        results = sp.next(results) if results["next"] else None
    return tracks
