"""Where config, profile and runtime data live.

By default config.yaml and profile.yaml go to the user's config directory and the database,
cache and output to the user's data directory (platformdirs, so it is right on Windows, macOS
and Linux). Setting JAC_HOME or passing --home puts everything in one folder instead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from jac.config import Config, load_config

APP = "job-alert-copilot"


@dataclass(frozen=True)
class Locations:
    config_dir: Path
    data_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.yaml"

    @property
    def profile_file(self) -> Path:
        return self.config_dir / "profile.yaml"

    def db(self, cfg: Config) -> Path:
        return self.data_dir / cfg.paths.db

    def out(self, cfg: Config) -> Path:
        return self.data_dir / cfg.paths.out

    def cache(self, cfg: Config) -> Path:
        return self.data_dir / cfg.paths.cache

    def resolve(self, path: str) -> Path:
        """A path from config.yaml: absolute stays, relative is under the config directory."""
        candidate = Path(path).expanduser()
        return candidate if candidate.is_absolute() else self.config_dir / candidate


def locations(home: str | None = None) -> Locations:
    """Resolve locations from --home, then JAC_HOME, then the platform directories."""
    chosen = home or os.environ.get("JAC_HOME")
    if chosen:
        folder = Path(chosen).expanduser().resolve()
        return Locations(folder, folder)
    return Locations(Path(user_config_dir(APP)), Path(user_data_dir(APP)))


def load(home: str | None = None) -> tuple[Locations, Config]:
    loc = locations(home)
    return loc, load_config(loc.config_file)
