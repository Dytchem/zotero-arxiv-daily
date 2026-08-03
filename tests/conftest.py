"""Root conftest: config fixtures and shared helpers.

All mocking uses pytest monkeypatch + SimpleNamespace. No unittest.mock.
"""

from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf

_CONFIG_DIR = str(Path(__file__).resolve().parent.parent / "config")


@pytest.fixture(scope="session")
def _base_config():
    """Session-scoped Hydra config with all required values filled in.

    Never mutate this directly; use the function-scoped ``config`` fixture.
    """
    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=_CONFIG_DIR, version_base=None):
        cfg = compose(
            config_name="default",
            overrides=[
                "zotero.user_id=000000",
                "zotero.api_key=fake-zotero-key",
                "zotero.include_path=null",
                "zotero.ignore_path=null",
                "email.sender=test@example.com",
                "email.receiver=test@example.com",
                "email.smtp_server=localhost",
                "email.smtp_port=1025",
                "email.sender_password=test",
                "llm.api.key=sk-fake",
                "llm.api.base_url=http://localhost:30000/v1",
                "llm.generation_kwargs.model=gpt-4o-mini",
                "llm.harness.engine=python", # tests exercise the deterministic Python harness (mock client); Pi engine is integration-tested in CI
                "reranker.api.key=sk-fake",
                "reranker.api.base_url=http://localhost:30000/v1",
                "reranker.api.model=text-embedding-3-large",
                "source.arxiv.category=[cs.AI,cs.CV]",
                "executor.source=[arxiv]",
                "executor.reranker=api",
                "executor.debug=false",
                "executor.send_empty=false",
            ],
        )
    return cfg


@pytest.fixture()
def config(_base_config):
    """Function-scoped, fully mutable copy of the session config.

    Hydra's compose() freezes the resulting tree, so deepcopy keeps the
    read-only markers; ``OmegaConf.structured(to_container(struct))`` lifts
    them recursively so tests can ``setattr`` arbitrary leaves (e.g.
    ``config.executor.cache_dir``) without hitting read-only errors.
    """
    container = OmegaConf.to_container(_base_config, resolve=True)
    return OmegaConf.structured(container)
