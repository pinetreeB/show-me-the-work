from __future__ import annotations

import importlib
import sys
import warnings

import smtw as _smtw


_SUBMODULES = (
    "brief",
    "card",
    "check",
    "check_support",
    "cli",
    "design_check",
    "doctor",
    "goals",
    "init",
    "intent",
    "migrate",
    "quarantine",
    "scorecard",
    "scorecard_observations",
    "versioning",
)

if not getattr(_smtw, "_fable_lite_deprecation_warned", False):
    warnings.warn(
        "fable_lite is deprecated; import smtw instead",
        DeprecationWarning,
        stacklevel=2,
    )
    _smtw._fable_lite_deprecation_warned = True

# COMPAT-03: only the interpreter invocation prefix may carry `-m`.  Scanning
# all of sys.orig_argv misreads ordinary application arguments (`python app.py
# --example -m fable_lite.cli`) as an interpreter module request and breaks the
# plain-import identity below.
_INTERPRETER_OPTIONS_WITH_VALUE = frozenset({"-W", "-X", "-c", "-m"})


def _requested_module_from_orig_argv(argv: list[str]) -> str:
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "-m":
            return argv[index + 1] if index + 1 < len(argv) else ""
        if token.startswith("-") and token != "-":
            index += (
                2
                if token in _INTERPRETER_OPTIONS_WITH_VALUE and "=" not in token
                else 1
            )
            continue
        # First non-option argument is the script name; everything after it is
        # application argv, not interpreter options.
        return ""
    return ""


_requested_module = _requested_module_from_orig_argv(sys.orig_argv)
_executed_submodule = (
    _requested_module.removeprefix("fable_lite.")
    if _requested_module.startswith("fable_lite.")
    else ""
)

for _name in _SUBMODULES:
    if _name == _executed_submodule:
        continue
    _module = importlib.import_module(f"smtw.{_name}")
    sys.modules[f"fable_lite.{_name}"] = _module

if not _executed_submodule:
    sys.modules[__name__] = _smtw
