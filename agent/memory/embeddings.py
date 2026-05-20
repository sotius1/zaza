"""Embedding provider abstraction for the ZAZA memory subsystem.

Provider chain (first available wins, no hard dependencies):

    1. ``ZazaEmbeddingProvider``      via the user's configured LLM provider
                                       endpoint (``/v1/embeddings``) — same
                                       credentials as the chat model
    2. ``OllamaEmbeddingProvider``    local Ollama at OLLAMA_HOST (default
                                       http://127.0.0.1:11434)
                                       model: ``nomic-embed-text``
    3. ``SentenceTransformersProvider`` lazy import — needs the optional
                                         dependency
    4. ``HashFallbackProvider``       deterministic projection of token
                                       hashes onto a fixed-dim vector. Not
                                       real semantic similarity but enough
                                       to keep recall functional offline.

Design notes:

* Embedding dimension is **provider-fixed**.  Each provider owns its dim;
  the ``MemoryStore`` checks for dim consistency at write time so we
  never mix vectors of different dimensionality in the same column.
* All providers are *batchable*.  The default ``embed_batch`` falls back
  to per-item ``embed`` when the underlying API doesn't natively batch.
* All errors degrade gracefully — a transient network blip should fall
  through to the next provider rather than corrupt the memory store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class Embedding(ABC):
    """Abstract embedding provider."""

    name: str = "abstract"
    dim: int = 0   # subclasses set their fixed output dimension

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Return a single embedding vector."""

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch.  Default: per-item loop."""
        return [self.embed(t) for t in texts]

    def is_available(self) -> bool:
        """Provider readiness check.  Default: True."""
        return True


# ---------------------------------------------------------------------------
# Provider 1: ZAZA / OpenAI-compatible /v1/embeddings
# ---------------------------------------------------------------------------

class OpenAICompatibleEmbeddingProvider(Embedding):
    """Hits any OpenAI-compatible ``/v1/embeddings`` endpoint.

    Reads credentials from the same config the chat model uses:
    ``model.base_url`` + ``model.api_key`` from the active config.
    The user can override the embedding model via
    ``memory.embedding_model`` (default: ``zaza-embed`` — falls back to
    the OpenAI default ``text-embedding-3-small`` if the server doesn't
    know that name).
    """

    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str = "zaza-embed", dim: int = 1536):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.dim = dim
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self._base_url}/embeddings",
                method="OPTIONS",
                headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else {},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                self._available = resp.status < 500
        except Exception:
            self._available = False
        return bool(self._available)

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        import urllib.request

        payload = json.dumps({
            "model": self._model,
            "input": list(texts),
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("OpenAI-compatible embeddings failed: %s", exc)
            self._available = False
            raise

        return [item["embedding"] for item in data.get("data", [])]


# ---------------------------------------------------------------------------
# Provider 2: Ollama
# ---------------------------------------------------------------------------

class OllamaEmbeddingProvider(Embedding):
    """Local Ollama with ``nomic-embed-text`` (or any pulled embedding model)."""

    name = "ollama"
    dim = 768  # nomic-embed-text dim

    def __init__(self, host: Optional[str] = None, model: str = "nomic-embed-text"):
        self._host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        self._model = model
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self._host}/api/tags", timeout=1.5) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
            names = [m.get("name", "") for m in tags.get("models", [])]
            self._available = any(self._model in n for n in names)
        except Exception:
            self._available = False
        return bool(self._available)

    def embed(self, text: str) -> List[float]:
        import urllib.request

        payload = json.dumps({"model": self._model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._host}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("Ollama embedding failed: %s", exc)
            self._available = False
            raise
        return list(data.get("embedding", []))


# ---------------------------------------------------------------------------
# Provider 3: sentence-transformers (optional)
# ---------------------------------------------------------------------------

class SentenceTransformersProvider(Embedding):
    """Local embedding via the ``sentence-transformers`` package.

    Lazy-imports.  If the package isn't installed, ``is_available()``
    returns False and the chain falls through to the hash fallback.
    """

    name = "sentence-transformers"
    dim = 384  # all-MiniLM-L6-v2 dim

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import sentence_transformers  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
        return bool(self._available)

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, text: str) -> List[float]:
        m = self._load()
        return m.encode([text], convert_to_numpy=True)[0].tolist()

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        m = self._load()
        return [v.tolist() for v in m.encode(list(texts), convert_to_numpy=True)]


# ---------------------------------------------------------------------------
# Provider 4: hash-based fallback (always available, never best)
# ---------------------------------------------------------------------------

class HashFallbackProvider(Embedding):
    """Deterministic hash projection — last-resort fallback.

    The vector is built by hashing each token (whitespace-split, lowercased)
    into a fixed-dim space and L2-normalising.  Real semantic similarity
    is poor — collocations (e.g. "next.js" vs "nextjs") cluster only by
    coincidence — but the dimensionality and value range are identical to
    a real embedding, so all downstream code (vec search, cosine sim)
    works without special-casing.

    Used when:
    * the user is offline
    * Ollama / sentence-transformers aren't installed
    * the configured embedding endpoint is down

    The fallback writes its name into the row's ``embedding_model`` so the
    consolidator can re-embed those rows once a real provider becomes
    available.
    """

    name = "hash-fallback"
    dim = 256

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for token in (text or "").lower().split():
            h = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            for i in range(0, len(h), 4):
                idx = struct.unpack("<I", h[i:i + 4])[0] % self.dim
                vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


# ---------------------------------------------------------------------------
# Default chain resolution
# ---------------------------------------------------------------------------

_DEFAULT_EMBEDDER: Optional[Embedding] = None


def _try_provider(provider: Embedding) -> Optional[Embedding]:
    try:
        if provider.is_available():
            logger.info("Memory: using embedding provider '%s' (dim=%d)",
                        provider.name, provider.dim)
            return provider
    except Exception:
        logger.debug("Embedding provider '%s' unavailable", provider.name, exc_info=True)
    return None


def get_default_embedder(*, refresh: bool = False) -> Embedding:
    """Return the singleton embedding provider, picking the best available.

    Probe order: OpenAI-compatible (configured endpoint) → Ollama →
    sentence-transformers → hash fallback.
    """
    global _DEFAULT_EMBEDDER
    if _DEFAULT_EMBEDDER is not None and not refresh:
        return _DEFAULT_EMBEDDER

    candidates: List[Embedding] = []

    # 1. Configured LLM endpoint
    try:
        cfg = _read_active_config()
        base_url = (cfg.get("model", {}) or {}).get("base_url") or ""
        api_key = (cfg.get("model", {}) or {}).get("api_key") or ""
        emb_model = (cfg.get("memory", {}) or {}).get("embedding_model") or "zaza-embed"
        emb_dim = int((cfg.get("memory", {}) or {}).get("embedding_dim") or 1536)
        if base_url:
            candidates.append(OpenAICompatibleEmbeddingProvider(
                base_url=base_url, api_key=api_key, model=emb_model, dim=emb_dim,
            ))
    except Exception:
        logger.debug("Could not read active config for embeddings", exc_info=True)

    # 2. Ollama (if installed locally)
    candidates.append(OllamaEmbeddingProvider())

    # 3. sentence-transformers
    candidates.append(SentenceTransformersProvider())

    for c in candidates:
        chosen = _try_provider(c)
        if chosen is not None:
            _DEFAULT_EMBEDDER = chosen
            return _DEFAULT_EMBEDDER

    # 4. Always-available fallback
    _DEFAULT_EMBEDDER = HashFallbackProvider()
    logger.warning(
        "Memory: no real embedding provider available — falling back to "
        "hash projection. Install Ollama with `ollama pull nomic-embed-text` "
        "or set memory.embedding_model in config.yaml for semantic recall."
    )
    return _DEFAULT_EMBEDDER


def _read_active_config() -> dict:
    """Best-effort read of the live config.yaml."""
    try:
        from zaza_constants import get_zaza_home
        path = get_zaza_home() / "data" / "config.yaml"
    except Exception:
        from pathlib import Path
        path = Path.home() / ".agent-zaza" / "data" / "config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
