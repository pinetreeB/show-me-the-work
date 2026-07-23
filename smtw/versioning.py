from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import distribution as _distribution
from importlib.metadata import version as _installed_version
from pathlib import Path
import re
from typing import Final


_VERSION_RE: Final = re.compile(
    r'^version\s*=\s*"(?P<version>[^"]+)"',
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class VersionDiagnostics:
    tool_version: str
    module_version: str
    module_path: str
    distribution_version: str
    distribution_path: str
    source_checkout: bool
    mismatch: bool


def package_version() -> str:
    """Return the code version, preferring an adjacent source checkout."""
    source = _source_version()
    if source is not None:
        return source
    try:
        return _installed_version("fable-lite")
    except PackageNotFoundError:
        return "unknown"


def version_diagnostics() -> VersionDiagnostics:
    source = _source_version()
    source_checkout = source is not None
    tool_version = source if source is not None else package_version()
    try:
        installed = _installed_version("fable-lite")
    except PackageNotFoundError:
        installed = "not-installed"
    distribution_path = _distribution_path()
    mismatch = (
        installed not in {"not-installed", "unknown"}
        and tool_version not in {"not-installed", "unknown"}
        and installed != tool_version
    )
    return VersionDiagnostics(
        tool_version=tool_version,
        module_version=tool_version,
        module_path=str(Path(__file__).resolve().parent),
        distribution_version=installed,
        distribution_path=distribution_path,
        source_checkout=source_checkout,
        mismatch=mismatch,
    )


def _source_version() -> str | None:
    root = Path(__file__).resolve().parents[1]
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file() or not (root / ".git").exists():
        return None
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _VERSION_RE.search(text)
    return match.group("version") if match else None


def _distribution_path() -> str:
    try:
        located = _distribution("fable-lite").locate_file("")
    except PackageNotFoundError:
        return "not-installed"
    return str(Path(located).resolve())
