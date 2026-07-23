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
    "goals",
    "intent",
    "migrate",
    "quarantine",
    "scorecard",
    "scorecard_observations",
)

if not getattr(_smtw, "_fable_lite_deprecation_warned", False):
    warnings.warn(
        "fable_lite is deprecated; import smtw instead",
        DeprecationWarning,
        stacklevel=2,
    )
    _smtw._fable_lite_deprecation_warned = True

for _name in _SUBMODULES:
    _module = importlib.import_module(f"smtw.{_name}")
    sys.modules[f"fable_lite.{_name}"] = _module

sys.modules[__name__] = _smtw
