import hashlib
import json
from pathlib import Path
from time import sleep

import numpy as np
from loguru import logger
from openai import OpenAI

from .base import BaseReranker, register_reranker


@register_reranker("api")
class ApiReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        client = OpenAI(api_key=self.config.reranker.api.key, base_url=self.config.reranker.api.base_url)
        batch_size = self.config.reranker.api.get("batch_size") or 64
        model = self.config.reranker.api.model

        # The Zotero corpus barely changes between runs: cache its embeddings
        # keyed by content hash so we don't re-pay the API every single day.
        corpus_embeddings = self._load_corpus_cache(s2, model)
        if corpus_embeddings is None:
            all_embeddings = self._embed(client, batch_size, model, s1 + s2)
            corpus_embeddings = all_embeddings[len(s1):]
            self._save_corpus_cache(s2, model, corpus_embeddings)
        s1_embeddings = self._embed(client, batch_size, model, s1)

        s1_embeddings = np.array(s1_embeddings)
        s2_embeddings = np.array(corpus_embeddings)
        s1_embeddings_normalized = s1_embeddings / np.linalg.norm(s1_embeddings, axis=1, keepdims=True)
        s2_embeddings_normalized = s2_embeddings / np.linalg.norm(s2_embeddings, axis=1, keepdims=True)
        sim = np.dot(s1_embeddings_normalized, s2_embeddings_normalized.T)
        return sim

    @staticmethod
    def _embed(client: OpenAI, batch_size: int, model: str, texts: list[str]) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            # Transient provider errors (429 / 5xx / network blips) are common
            # enough that one retry with a short backoff is worth it before
            # the pipeline-level fallback kicks in.
            for attempt in range(3):
                try:
                    response = client.embeddings.create(input=batch, model=model)
                    break
                except Exception as exc:
                    if attempt == 2:
                        raise
                    logger.warning(f"Embedding API call failed (attempt {attempt + 1}/3): {exc}; retrying")
                    sleep(2 * (attempt + 1))
            all_embeddings.extend(r.embedding for r in response.data)
        return all_embeddings

    def _corpus_cache_path(self) -> Path:
        cache_dir = self.config.reranker.api.get("cache_dir") or ".cache"
        return Path(cache_dir) / "corpus_embeddings.json"

    @staticmethod
    def _corpus_cache_key(s2: list[str], model: str) -> str:
        h = hashlib.sha256()
        for text in s2:
            h.update(text.encode("utf-8", errors="ignore"))
        h.update(b"|" + model.encode("utf-8"))
        return h.hexdigest()

    def _load_corpus_cache(self, s2: list[str], model: str) -> list[list[float]] | None:
        path = self._corpus_cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if data.get("key") == self._corpus_cache_key(s2, model) and data.get("model") == model:
                return data["embeddings"]
        except Exception as exc:
            logger.warning(f"Failed to load embedding cache: {exc}")
        return None

    def _save_corpus_cache(self, s2: list[str], model: str, embeddings: list[list[float]]) -> None:
        try:
            path = self._corpus_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "key": self._corpus_cache_key(s2, model),
                "model": model,
                "embeddings": embeddings,
            }))
            tmp.replace(path)
        except Exception as exc:
            logger.warning(f"Failed to save embedding cache: {exc}")
