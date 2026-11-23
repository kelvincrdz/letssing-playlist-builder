"""Caminhos da aplicação.

`data/` guarda config e banco. `workspace/` é a única área onde a app cria e
apaga arquivos do pipeline (input, output, tools, cache, zip).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_WORKSPACE = PROJECT_ROOT / "workspace"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def input(self) -> Path:
        return self.root / "input"

    @property
    def output(self) -> Path:
        # Pasta dedicada: o conversor a apaga inteira antes de cada execução.
        return self.root / "output"

    @property
    def stage(self) -> Path:
        # Entrada do conversor (ele grava o cache dele aqui e renomeia pastas)
        return self.root / "stage"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def dist(self) -> Path:
        return self.root / "dist"

    def ensure(self) -> None:
        for p in (self.input, self.stage, self.output, self.tools, self.dist):
            p.mkdir(parents=True, exist_ok=True)

    def safe_rmtree(self, target: Path) -> None:
        """Apaga `target` somente se estiver estritamente dentro do workspace."""
        root = self.root.resolve()
        resolved = target.resolve()
        if resolved == root or root not in resolved.parents:
            raise PermissionError(f"Recusado: {resolved} está fora de {root}")
        if resolved.exists():
            shutil.rmtree(resolved)
