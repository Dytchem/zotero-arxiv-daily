"""Tests for ApiReranker — uses stub OpenAI client via monkeypatch."""

from zotero_arxiv_daily.reranker.api import ApiReranker


def test_api_reranker_similarity_shape(config, patch_openai):
    reranker = ApiReranker(config)
    score = reranker.get_similarity_score(["hello", "world"], ["ping"])
    assert score.shape == (2, 1)


def test_api_reranker_batching(config, patch_openai):
    reranker = ApiReranker(config)
    s1 = [f"text {i}" for i in range(5)]
    s2 = [f"corpus {i}" for i in range(3)]
    score = reranker.get_similarity_score(s1, s2)
    assert score.shape == (5, 3)


def test_corpus_embedding_cache_reused(config, monkeypatch, tmp_path):
    """Same corpus skips re-embedding; only candidates hit the API."""
    from types import SimpleNamespace

    from omegaconf import open_dict

    calls = {"count": 0, "inputs": []}

    def recording_create(**kwargs):
        calls["count"] += 1
        calls["inputs"].append(kwargs["input"])
        n = len(kwargs["input"]) if isinstance(kwargs["input"], list) else 1
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3], index=i, object="embedding") for i in range(n)],
            model="text-embedding-3-large",
            object="list",
        )

    stub = SimpleNamespace(embeddings=SimpleNamespace(create=recording_create))
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub)

    with open_dict(config.reranker.api):
        config.reranker.api.cache_dir = str(tmp_path)

    reranker = ApiReranker(config)
    corpus = ["corpus 1", "corpus 2", "corpus 3"]

    # First run: cache miss -> one batch with candidate + corpus
    reranker.get_similarity_score(["candidate A"], corpus)
    assert len(calls["inputs"][0]) == 4  # 1 candidate + 3 corpus

    # Second run with the same corpus: only the candidate is embedded
    calls["count"] = 0
    calls["inputs"] = []
    reranker.get_similarity_score(["candidate B"], corpus)
    assert calls["count"] == 1
    assert calls["inputs"][0] == ["candidate B"]
