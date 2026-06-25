#!/usr/bin/env python3
"""Generate config/custom.yaml from environment variables.
This bypasses OmegaConf interpolation issues."""
import os, yaml, sys

cfg = {
    "zotero": {
        "user_id": os.environ.get("ZOTERO_ID", "").strip(),
        "api_key": os.environ.get("ZOTERO_KEY", "").strip(),
        "include_path": None,
    },
    "email": {
        "sender": os.environ.get("SENDER", "").strip(),
        "receiver": os.environ.get("RECEIVER", "").strip(),
        "smtp_server": "smtp.qq.com",
        "smtp_port": 465,
        "sender_password": os.environ.get("SENDER_PASSWORD", "").strip(),
    },
    "llm": {
        "api": {
            "key": os.environ.get("OPENAI_API_KEY", "").strip(),
            "base_url": os.environ.get("OPENAI_API_BASE", "").strip(),
        },
        "generation_kwargs": {"model": "gpt-4o-mini"},
    },
    "source": {
        "arxiv": {
            "category": ["cs.AI", "cs.CV", "cs.LG", "cs.CL"],
        }
    },
    "executor": {
        "debug": True,
        "source": ["arxiv"],
    },
}

with open("config/custom.yaml", "w") as f:
    yaml.dump(cfg, f, default_flow_style=False)

print("Generated config/custom.yaml")
sys.exit(0)
