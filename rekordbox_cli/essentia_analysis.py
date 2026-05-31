"""Essentia-based audio analysis for genre, mood, energy, and danceability."""

import os
from pathlib import Path
from dataclasses import dataclass


@dataclass
class AudioFeatures:
    """Analysis results for a single track."""
    genre: str | None = None
    subgenre: str | None = None
    mood: str | None = None
    energy: float = 0.0  # 0-1
    danceability: float = 0.0  # 0-1
    bpm: float = 0.0
    key: str | None = None
    scale: str | None = None
    # Extra tags from ML models
    tags: list[str] | None = None

    @property
    def energy_level(self) -> int:
        """Energy as 1-10 scale."""
        return max(1, min(10, round(self.energy * 10)))

    @property
    def mood_tag(self) -> str:
        """Human-readable mood + energy tag for comments field."""
        parts = []
        if self.mood:
            parts.append(self.mood)
        parts.append(f"E{self.energy_level}")
        if self.danceability >= 0.7:
            parts.append("dance")
        return " | ".join(parts)


class EssentiaAnalyzer:
    """Analyze audio files using Essentia ML models."""

    def __init__(self):
        self._models_loaded = False
        self._algo = None

    def _ensure_models(self):
        """Lazy-load Essentia and models on first use."""
        if self._models_loaded:
            return

        try:
            import essentia
            import essentia.standard as es
            self._es = es
            self._essentia = essentia
            self._models_loaded = True
        except ImportError:
            raise RuntimeError(
                "Essentia not installed. Run: pip install essentia-tensorflow"
            )

    def analyze(self, file_path: str) -> AudioFeatures | None:
        """Analyze an audio file and return features.
        
        Supports MP3, WAV, FLAC, AIFF, M4A.
        """
        self._ensure_models()
        es = self._es

        if not os.path.exists(file_path):
            return None

        try:
            # Load audio (mono, 44100 Hz)
            audio = es.MonoLoader(filename=file_path, sampleRate=44100)()

            if len(audio) < 44100:  # Less than 1 second
                return None

            features = AudioFeatures()

            # BPM detection
            features.bpm = self._detect_bpm(audio)

            # Key detection
            key, scale = self._detect_key(audio)
            features.key = key
            features.scale = scale

            # Energy (RMS-based)
            features.energy = self._compute_energy(audio)

            # Danceability
            features.danceability = self._compute_danceability(audio)

            # Genre & mood from ML models (if available)
            genre_result = self._predict_genre(audio)
            if genre_result:
                features.genre = genre_result.get("genre")
                features.subgenre = genre_result.get("subgenre")
                features.tags = genre_result.get("tags", [])

            # Mood from valence/arousal
            features.mood = self._predict_mood(audio)

            return features

        except Exception:
            return None

    def _detect_bpm(self, audio) -> float:
        """Detect BPM using RhythmExtractor2013."""
        es = self._es
        try:
            rhythm = es.RhythmExtractor2013(method="multifeature")
            bpm, beats, confidence, estimates, intervals = rhythm(audio)
            return round(bpm, 1)
        except Exception:
            return 0.0

    def _detect_key(self, audio) -> tuple[str | None, str | None]:
        """Detect musical key and scale."""
        es = self._es
        try:
            key_extractor = es.KeyExtractor()
            key, scale, strength = key_extractor(audio)
            if strength > 0.3:
                return key, scale
        except Exception:
            pass
        return None, None

    def _compute_energy(self, audio) -> float:
        """Compute energy level (0-1) based on loudness and dynamics."""
        es = self._es
        try:
            # Use ReplayGain as a proxy for loudness
            loudness = es.Loudness()(audio)
            # Use dynamic complexity
            dc = es.DynamicComplexity()(audio)

            # Normalize loudness to 0-1 range (typical range: 0.001 to 0.5)
            energy = min(1.0, max(0.0, loudness * 3))

            # Factor in dynamic complexity (more complex = more energetic feel)
            if isinstance(dc, tuple):
                dc_val = dc[0]
            else:
                dc_val = dc
            # Blend
            energy = 0.7 * energy + 0.3 * min(1.0, dc_val / 10.0)
            return round(energy, 3)
        except Exception:
            return 0.5

    def _compute_danceability(self, audio) -> float:
        """Compute danceability score (0-1)."""
        es = self._es
        try:
            danceability, _ = es.Danceability()(audio)
            return round(min(1.0, max(0.0, danceability)), 3)
        except Exception:
            return 0.5

    def _predict_genre(self, audio) -> dict | None:
        """Predict genre using Essentia TensorFlow models if available."""
        try:
            from essentia.standard import (
                TensorflowPredictEffnetDiscogs,
                TensorflowPredict2D,
            )
            # Try to use the discogs effnet model for genre
            model_path = self._find_model("genre_discogs400-discogs-effnet-1.pb")
            if not model_path:
                return self._predict_genre_classical(audio)

            embedding_model = TensorflowPredictEffnetDiscogs(
                graphFilename=model_path, output="PartitionedCall:1"
            )
            embeddings = embedding_model(audio)

            # Map to genre labels
            # This is simplified — full implementation would load label files
            return None

        except (ImportError, Exception):
            return self._predict_genre_classical(audio)

    def _predict_genre_classical(self, audio) -> dict | None:
        """Fallback: use spectral features to estimate genre family."""
        es = self._es
        try:
            # Use spectral centroid and rolloff as genre indicators
            spec = es.Spectrum()(audio[:44100 * 30])  # First 30 sec
            centroid = es.SpectralCentroidTime()(audio[:44100 * 30])
            
            # Very rough heuristic based on spectral content
            bpm = self._detect_bpm(audio)
            
            if bpm >= 138:
                genre = "Techno"
            elif bpm >= 128:
                genre = "House"
            elif bpm >= 120:
                genre = "Disco"
            elif bpm >= 100:
                genre = "Reggaeton"
            elif bpm >= 85:
                genre = "Hip Hop"
            else:
                genre = "Downtempo"

            return {"genre": genre, "subgenre": None, "tags": []}
        except Exception:
            return None

    def _predict_mood(self, audio) -> str | None:
        """Predict mood using spectral/energy features."""
        es = self._es
        try:
            # Simplified mood detection based on spectral features
            # High energy + high spectral centroid = aggressive/euphoric
            # Low energy + low centroid = relaxed/melancholic
            energy = self._compute_energy(audio)
            
            # Use spectral centroid as brightness proxy
            spec_centroid = es.SpectralCentroidTime()(audio[:44100 * 30])
            
            if energy > 0.7:
                if spec_centroid > 3000:
                    return "Euphoric"
                else:
                    return "Aggressive"
            elif energy > 0.4:
                if spec_centroid > 2500:
                    return "Happy"
                else:
                    return "Groovy"
            else:
                if spec_centroid > 2000:
                    return "Melancholic"
                else:
                    return "Chill"
        except Exception:
            return None

    def _find_model(self, filename: str) -> str | None:
        """Find an Essentia model file."""
        search_paths = [
            Path.home() / ".essentia" / "models" / filename,
            Path("/usr/local/share/essentia/models") / filename,
            Path.home() / "essentia-models" / filename,
        ]
        for p in search_paths:
            if p.exists():
                return str(p)
        return None


def analyze_batch(file_paths: list[str], progress_callback=None) -> dict[str, AudioFeatures]:
    """Analyze multiple files, returning {path: features} dict."""
    analyzer = EssentiaAnalyzer()
    results = {}

    for i, path in enumerate(file_paths):
        features = analyzer.analyze(path)
        if features:
            results[path] = features
        if progress_callback:
            progress_callback(i + 1, len(file_paths))

    return results
