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

_requested_module = ""
if "-m" in sys.orig_argv:
    _index = sys.orig_argv.index("-m")
    if _index + 1 < len(sys.orig_argv):
        _requested_module = sys.orig_argv[_index + 1]
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
