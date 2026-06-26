from .base import BaseReranker, register_reranker
import json
import numpy as np
from urllib.request import Request, urlopen
from urllib.parse import urlparse

@register_reranker("api")
class ApiReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        base_url = self.config.reranker.api.base_url.rstrip("/")
        key = self.config.reranker.api.key
        model = self.config.reranker.api.model
        batch_size = self.config.reranker.api.get("batch_size") or 64

        # Ollama native API: /api/embed (strip /v1 from OpenAI-compatible base)
        api_url = base_url.replace("/v1", "") + "/api/embed"

        all_texts = s1 + s2
        all_embeddings = []

        for i in range(0, len(all_texts), batch_size):
            batch = all_texts[i : i + batch_size]
            req = Request(
                api_url,
                data=json.dumps({"model": model, "input": batch}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
                method="POST",
            )
            with urlopen(req) as resp:
                data = json.loads(resp.read().decode())
            all_embeddings.extend(data["embeddings"])

        s1_embeddings = np.array(all_embeddings[: len(s1)])
        s2_embeddings = np.array(all_embeddings[len(s1) :])
        s1_embeddings_normalized = s1_embeddings / np.linalg.norm(
            s1_embeddings, axis=1, keepdims=True
        )
        s2_embeddings_normalized = s2_embeddings / np.linalg.norm(
            s2_embeddings, axis=1, keepdims=True
        )
        sim = np.dot(s1_embeddings_normalized, s2_embeddings_normalized.T)
        return sim
