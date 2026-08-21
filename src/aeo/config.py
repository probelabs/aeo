"""Load and write aeo-cli-config-v1 documents."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENGINES = ("claude", "codex", "grok")
ARMS = ("knowledge", "search")
DEFAULT_SAMPLES_PER_ARM = 1


@dataclass
class Prompt:
    id: str
    text: str
    intent: str | None = None


@dataclass
class Config:
    brand: str
    domain: str
    aliases: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=lambda: list(ENGINES))
    prompts: list[Prompt] = field(default_factory=list)
    cli: dict[str, str] = field(default_factory=dict)
    data_dir: str = "aeo-data"
    samples_per_arm: int = DEFAULT_SAMPLES_PER_ARM
    path: Path | None = None

    def cli_path(self, engine: str) -> str:
        return self.cli.get(engine, engine)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": "aeo-cli-config-v1",
            "brand": self.brand,
            "domain": self.domain,
            "aliases": list(self.aliases),
            "competitors": list(self.competitors),
            "engines": list(self.engines),
            "prompts": [
                {"id": p.id, "text": p.text, **({"intent": p.intent} if p.intent else {})}
                for p in self.prompts
            ],
            "data_dir": self.data_dir,
            "samples_per_arm": self.samples_per_arm,
        }
        if self.cli:
            out["cli"] = dict(self.cli)
        return out


def load_config(path: str | Path) -> Config:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config must be a JSON object: {p}")
    prompts = []
    for raw in data.get("prompts") or []:
        prompts.append(
            Prompt(
                id=str(raw["id"]),
                text=str(raw["text"]),
                intent=raw.get("intent"),
            )
        )
    engines = [e for e in (data.get("engines") or list(ENGINES)) if e in ENGINES]
    return Config(
        brand=str(data["brand"]),
        domain=str(data["domain"]),
        aliases=list(data.get("aliases") or []),
        competitors=list(data.get("competitors") or []),
        engines=engines or list(ENGINES),
        prompts=prompts,
        cli={k: str(v) for k, v in (data.get("cli") or {}).items()},
        data_dir=str(data.get("data_dir") or "aeo-data"),
        samples_per_arm=int(data.get("samples_per_arm") or DEFAULT_SAMPLES_PER_ARM),
        path=p,
    )


STARTER_PROMPTS = [
    {
        "id": "folder-by-content",
        "text": "What's the best way to search through a folder of files by content?",
        "intent": "local-folder-content-search",
    },
    {
        "id": "markdown-folder",
        "text": "What's the best way to search through a folder with markdown files?",
        "intent": "local-markdown-search",
    },
]


def starter_config(brand: str, domain: str) -> Config:
    aliases = [brand.lower(), domain]
    return Config(
        brand=brand,
        domain=domain,
        aliases=aliases,
        competitors=["ripgrep", "recoll", "elasticsearch"],
        engines=list(ENGINES),
        prompts=[Prompt(**p) for p in STARTER_PROMPTS],
    )


def write_config(cfg: Config, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")
    return p
