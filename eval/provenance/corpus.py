from __future__ import annotations

from dataclasses import replace
import random
from typing import Final, assert_never

from .models import CorpusCase, Family, Mutation, Origin, Polarity


POSITIVE_FAMILIES: Final = (
    Family.REDIRECT,
    Family.HEREDOC,
    Family.TEE,
    Family.SED_IN_PLACE,
    Family.CP_MV,
    Family.REMOVE,
    Family.POWERSHELL,
    Family.PYTHON_NODE,
    Family.USER_SCRIPT,
    Family.GENERATOR,
    Family.EXTERNAL_PROCESS,
    Family.STRUCTURED_EDIT,
)
POSITIVE_MUTATIONS: Final = (
    Mutation.CREATE,
    Mutation.APPEND,
    Mutation.TRUNCATE,
    Mutation.SAME_SIZE_MODIFY,
    Mutation.DELETE,
    Mutation.RENAME,
    Mutation.COPY,
    Mutation.MOVE,
    Mutation.MULTI_FILE,
    Mutation.SYMLINK,
)
NEGATIVE_FAMILIES: Final = (
    Family.CAT,
    Family.RIPGREP,
    Family.LIST,
    Family.GIT_STATUS,
    Family.FAILED,
    Family.PATH_MENTION,
    Family.SAME_BYTES,
    Family.OUTSIDE,
    Family.HARD_EXCLUDE,
    Family.REVERT,
)
PATH_VARIANTS: Final = (
    "src/app.py",
    "space dir/file name.txt",
    "한글/결과.txt",
    "unicode/é.txt",
    "nested/a/b/c.txt",
    "glob/item.tmp",
    "node_modules/pkg/forced.js",
    "absolute/target.txt",
    "links/link.txt",
    "dist/generated.js",
)


def golden_cases() -> tuple[CorpusCase, ...]:
    positives = tuple(
        _positive_case(family, mutation, family_index * len(POSITIVE_MUTATIONS) + mutation_index)
        for family_index, family in enumerate(POSITIVE_FAMILIES)
        for mutation_index, mutation in enumerate(POSITIVE_MUTATIONS)
    )
    negatives = tuple(
        _negative_case(family, family_index * 8 + variant_index)
        for family_index, family in enumerate(NEGATIVE_FAMILIES)
        for variant_index in range(8)
    )
    return positives + negatives


def randomized_cases(count: int, seed: int) -> tuple[CorpusCase, ...]:
    randomizer = random.Random(seed)
    cases = golden_cases()
    return tuple(
        replace(randomizer.choice(cases), case_id=f"random-{seed}-{index:04d}")
        for index in range(count)
    )


def _positive_case(family: Family, mutation: Mutation, index: int) -> CorpusCase:
    origin = _origin(family, mutation)
    target = _target(index, origin)
    return CorpusCase(
        f"positive-{index + 1:03d}",
        Polarity.POSITIVE,
        mutation,
        family,
        target,
        origin,
        target.startswith("node_modules/"),
        target.startswith("absolute/"),
        target.startswith("glob/"),
    )


def _negative_case(family: Family, index: int) -> CorpusCase:
    target = _target(index, Origin.SHELL).replace("positive", "negative")
    return CorpusCase(
        f"negative-{index + 1:03d}",
        Polarity.NEGATIVE,
        Mutation.NONE,
        family,
        target,
        Origin.SHELL,
        False,
        target.startswith("absolute/"),
        target.startswith("glob/"),
    )


def _origin(family: Family, mutation: Mutation) -> Origin:
    match family:
        case Family.STRUCTURED_EDIT:
            return Origin.EDIT
        case Family.GENERATOR:
            return Origin.GENERATED
        case Family.EXTERNAL_PROCESS:
            return Origin.OVERLAP if mutation is Mutation.CREATE else Origin.EXTERNAL
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
            | Family.CAT
            | Family.RIPGREP
            | Family.LIST
            | Family.GIT_STATUS
            | Family.FAILED
            | Family.PATH_MENTION
            | Family.SAME_BYTES
            | Family.OUTSIDE
            | Family.HARD_EXCLUDE
            | Family.REVERT
        ):
            return Origin.SHELL
        case unreachable:
            assert_never(unreachable)
            return Origin.SHELL


def _target(index: int, origin: Origin) -> str:
    match origin:
        case Origin.GENERATED:
            return f"dist/generated-positive-{index:03d}.js"
        case Origin.EDIT | Origin.SHELL | Origin.EXTERNAL | Origin.OVERLAP:
            path = PATH_VARIANTS[index % len(PATH_VARIANTS)]
            parent, name = path.rsplit("/", 1)
            return f"{parent}/positive-{index:03d}-{name}"
