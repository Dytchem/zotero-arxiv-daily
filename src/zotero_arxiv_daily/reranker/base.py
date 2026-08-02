from abc import ABC, abstractmethod

import numpy as np
from omegaconf import DictConfig

from ..protocol import CorpusPaper, Paper


class BaseReranker(ABC):
    def __init__(self, config:DictConfig):
        self.config = config

    def rerank(self, candidates:list[Paper], corpus:list[CorpusPaper]) -> list[Paper]:
        from ..utils import bm25_scores
        corpus = sorted(corpus,key=lambda x: x.added_date,reverse=True)
        time_decay_weight = 1 / (1 + np.log10(np.arange(len(corpus)) + 1))
        time_decay_weight: np.ndarray = time_decay_weight / time_decay_weight.sum()
        sim = self.get_similarity_score([c.abstract for c in candidates], [c.abstract for c in corpus])
        assert sim.shape == (len(candidates), len(corpus))
        # Hybrid lexical + vector scoring: pure embeddings can drift from the
        # user's research niche (upstream issue #245); BM25 anchors keywords.
        alpha = None
        if self.config is not None:
            alpha = self.config.executor.get("rerank_alpha", 0.7)
        if alpha is not None:
            bm25 = bm25_scores([c.abstract for c in candidates], [c.abstract for c in corpus])
            eps = 1e-9
            bm25_norm = (bm25 - bm25.min()) / (bm25.max() - bm25.min() + eps)
            sim = alpha * np.clip(sim, 0, 1) + (1 - alpha) * bm25_norm
        scores = (sim * time_decay_weight).sum(axis=1) * 10 # [n_candidate]
        for s,c in zip(scores,candidates, strict=True):
            c.score = s
        candidates = sorted(candidates,key=lambda x: x.score,reverse=True)
        return candidates
    
    @abstractmethod
    def get_similarity_score(self, s1:list[str], s2:list[str]) -> np.ndarray:
        raise NotImplementedError

registered_rerankers = {}

def register_reranker(name:str):
    def decorator(cls):
        registered_rerankers[name] = cls
        return cls
    return decorator

def get_reranker_cls(name:str) -> type[BaseReranker]:
    if name not in registered_rerankers:
        raise ValueError(f"Reranker {name} not found")
    return registered_rerankers[name]