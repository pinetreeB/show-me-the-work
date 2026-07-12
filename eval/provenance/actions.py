from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import assert_never

from .models import CorpusCase, Family, Mutation, Origin


@dataclass(frozen=True, slots=True)
class ActionLayout:
    root: Path
    target: Path
    source: Path
    outside: Path


def prepare(root: Path, case: CorpusCase) -> ActionLayout:
    target = root / case.target
    layout = ActionLayout(
        root,
        target,
        target.with_name(f"source-{target.name}"),
        root.parent / f"{case.case_id}-outside.txt",
    )
    if case.origin is Origin.GENERATED:
        _write(root / ".fable-lite" / "provenance-config.json", json.dumps({"version": 1, "generated": ["dist/**"]}))
    if case.positive:
        _prepare_positive(layout, case.mutation)
    else:
        _prepare_negative(layout, case.family)
    return layout


def execute(layout: ActionLayout, case: CorpusCase) -> None:
    if case.positive:
        _execute_positive(layout, case.mutation)
    else:
        _execute_negative(layout, case.family)


def command_for(case: CorpusCase, layout: ActionLayout) -> str:
    target = _display_path(case, layout.target)
    source = str(layout.source)
    match case.family:
        case Family.REDIRECT:
            return f"printf x > '{target}'"
        case Family.HEREDOC:
            return f"cat <<'EOF' > '{target}'\nbody\nEOF"
        case Family.TEE:
            return f"printf x | tee -a '{target}'"
        case Family.SED_IN_PLACE:
            return f"sed -i 's/a/b/' '{target}'"
        case Family.CP_MV:
            return _transfer_command(case.mutation, source, target)
        case Family.REMOVE:
            return f"rm '{target}'"
        case Family.POWERSHELL:
            return _powershell_command(case.mutation, target)
        case Family.PYTHON_NODE:
            return _inline_command(case.mutation, target)
        case Family.USER_SCRIPT:
            return f"./tools/write-output '{target}'"
        case Family.GENERATOR:
            return f"build-generator --out '{target}'"
        case Family.EXTERNAL_PROCESS:
            return f"opaque-writer --output '{target}'"
        case Family.STRUCTURED_EDIT:
            return ""
        case Family.CAT:
            return f"cat '{target}'"
        case Family.RIPGREP:
            return f"rg needle '{target}'"
        case Family.LIST:
            return f"ls '{target}'"
        case Family.GIT_STATUS:
            return "git status --short"
        case Family.FAILED:
            return f"false > '{target}'"
        case Family.PATH_MENTION:
            return f"echo would-write '{target}'"
        case Family.SAME_BYTES:
            return f"printf base > '{target}'"
        case Family.OUTSIDE:
            return f"printf outside > '{layout.outside}'"
        case Family.HARD_EXCLUDE:
            return ".fable-lite/internal-write"
        case Family.REVERT:
            return f"printf changed > '{target}'; printf base > '{target}'"
        case unreachable:
            assert_never(unreachable)


def canonical_command(case: CorpusCase) -> str:
    root = Path("C:/fable-provenance-replay")
    target = root / case.target
    return command_for(
        case,
        ActionLayout(root, target, target.with_name(f"source-{target.name}"), root / "outside.txt"),
    )


def cleanup(layout: ActionLayout) -> None:
    layout.outside.unlink(missing_ok=True)


def _prepare_positive(layout: ActionLayout, mutation: Mutation) -> None:
    match mutation:
        case Mutation.APPEND | Mutation.TRUNCATE | Mutation.SAME_SIZE_MODIFY | Mutation.DELETE:
            _write(layout.target, "base")
        case Mutation.RENAME | Mutation.COPY | Mutation.MOVE | Mutation.SYMLINK:
            _write(layout.source, "base")
        case Mutation.CREATE | Mutation.MULTI_FILE:
            return
        case Mutation.NONE:
            raise AssertionError("positive corpus case needs a mutation")
        case unreachable:
            assert_never(unreachable)


def _prepare_negative(layout: ActionLayout, family: Family) -> None:
    match family:
        case Family.SAME_BYTES | Family.REVERT:
            _write(layout.target, "base")
        case (
            Family.CAT
            | Family.RIPGREP
            | Family.LIST
            | Family.GIT_STATUS
            | Family.FAILED
            | Family.PATH_MENTION
            | Family.OUTSIDE
            | Family.HARD_EXCLUDE
        ):
            return
        case (
            Family.REDIRECT
            | Family.HEREDOC
            | Family.TEE
            | Family.SED_IN_PLACE
            | Family.CP_MV
            | Family.REMOVE
            | Family.POWERSHELL
            | Family.PYTHON_NODE
            | Family.USER_SCRIPT
            | Family.GENERATOR
            | Family.EXTERNAL_PROCESS
            | Family.STRUCTURED_EDIT
        ):
            raise AssertionError("positive family cannot prepare a negative case")
        case unreachable:
            assert_never(unreachable)


def _execute_positive(layout: ActionLayout, mutation: Mutation) -> None:
    match mutation:
        case Mutation.CREATE:
            _write(layout.target, "created")
        case Mutation.APPEND:
            _append(layout.target, "-append")
        case Mutation.TRUNCATE:
            _write(layout.target, "")
        case Mutation.SAME_SIZE_MODIFY:
            metadata = layout.target.stat()
            _write(layout.target, "other")
            os.utime(layout.target, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        case Mutation.DELETE:
            layout.target.unlink()
        case Mutation.RENAME | Mutation.MOVE:
            layout.source.replace(layout.target)
        case Mutation.COPY:
            layout.target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(layout.source, layout.target)
        case Mutation.MULTI_FILE:
            _write(layout.target, "first")
            _write(layout.target.with_name(f"second-{layout.target.name}"), "second")
        case Mutation.SYMLINK:
            layout.target.parent.mkdir(parents=True, exist_ok=True)
            layout.target.symlink_to(layout.source.name)
        case Mutation.NONE:
            raise AssertionError("positive corpus case needs a mutation")
        case unreachable:
            assert_never(unreachable)


def _execute_negative(layout: ActionLayout, family: Family) -> None:
    match family:
        case Family.SAME_BYTES:
            _write(layout.target, "base")
        case Family.REVERT:
            _write(layout.target, "changed")
            _write(layout.target, "base")
        case Family.OUTSIDE:
            _write(layout.outside, "outside")
        case Family.HARD_EXCLUDE:
            _write(layout.root / ".fable-lite" / "hidden.txt", "hidden")
        case (
            Family.CAT
            | Family.RIPGREP
            | Family.LIST
            | Family.GIT_STATUS
            | Family.FAILED
            | Family.PATH_MENTION
        ):
            return
        case (
            Family.REDIRECT
            | Family.HEREDOC
            | Family.TEE
            | Family.SED_IN_PLACE
            | Family.CP_MV
            | Family.REMOVE
            | Family.POWERSHELL
            | Family.PYTHON_NODE
            | Family.USER_SCRIPT
            | Family.GENERATOR
            | Family.EXTERNAL_PROCESS
            | Family.STRUCTURED_EDIT
        ):
            raise AssertionError("positive family cannot execute a negative case")
        case unreachable:
            assert_never(unreachable)


def _transfer_command(mutation: Mutation, source: str, target: str) -> str:
    match mutation:
        case Mutation.COPY:
            return f"cp '{source}' '{target}'"
        case Mutation.MOVE | Mutation.RENAME:
            return f"mv '{source}' '{target}'"
        case _:
            return f"cp '{source}' '{target}'"


def _powershell_command(mutation: Mutation, target: str) -> str:
    match mutation:
        case Mutation.CREATE:
            return f"Set-Content -LiteralPath '{target}' -Value x"
        case Mutation.APPEND:
            return f"Add-Content -Path '{target}' -Value x"
        case _:
            return f"Out-Content -FilePath '{target}' -InputObject x"


def _inline_command(mutation: Mutation, target: str) -> str:
    match mutation:
        case Mutation.DELETE:
            return f"node -e \"fs.rmSync('{target}')\""
        case Mutation.RENAME | Mutation.MOVE:
            return f"node -e \"fs.renameSync('source', '{target}')\""
        case _:
            return f"python -c \"from pathlib import Path; Path('{target}').write_text('x')\""


def _display_path(case: CorpusCase, path: Path) -> str:
    if case.glob_hint:
        return "glob/*.tmp"
    return str(path) if case.absolute_hint else case.target


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        _ = handle.write(text)
