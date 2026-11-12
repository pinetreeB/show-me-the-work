"""Synthetic operator scope; never reads installed settings or real cases."""
import json


def make_scope(tmp_path):
    path = tmp_path / "judge-scope.json"
    if not path.exists():
        value = {"version": 1, "cases": {
            "example-case": ["~/example-repo/"],
            "other-case": ["~/other-repo/"]},
            "repositories": [
                {"case": "example-case", "root": str(tmp_path / "example-repo"),
                 "prefix": "~/example-repo/", "revision": "a" * 40},
                {"case": "other-case", "root": str(tmp_path / "other-repo"),
                 "prefix": "~/other-repo/", "revision": "b" * 40}]}
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return path
