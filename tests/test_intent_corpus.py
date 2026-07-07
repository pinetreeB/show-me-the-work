from __future__ import annotations

import json
from pathlib import Path
from typing import TypeAlias

from core.ambiguity import evaluate_ambiguity


ROOT = Path(__file__).resolve().parents[1]

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
CorpusItem: TypeAlias = dict[str, JsonValue]


def load_corpus() -> list[CorpusItem]:
    raw = json.loads((ROOT / "eval" / "intent-corpus.json").read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return [item for item in raw if isinstance(item, dict)]


def prepare_context(root: Path, item: CorpusItem) -> None:
    context = item.get("context")
    if not isinstance(context, dict):
        return
    state_dir = root / ".fable-lite"
    state_dir.mkdir(parents=True, exist_ok=True)
    if context.get("has_goals") is True:
        (state_dir / "goals.json").write_text("{}", encoding="utf-8", newline="\n")
    if context.get("has_intent") is True:
        (state_dir / "intent.json").write_text("{}", encoding="utf-8", newline="\n")


def test_intent_corpus_has_zero_false_positives_and_at_most_two_false_negatives(tmp_path: Path) -> None:
    false_positives: list[str] = []
    false_negatives: list[str] = []

    for index, item in enumerate(load_corpus()):
        prompt = item.get("prompt")
        expected = item.get("expected")
        assert isinstance(prompt, str)
        assert expected in {"ambiguous", "clear"}
        case_root = tmp_path / f"case-{index:02d}"
        prepare_context(case_root, item)

        result = evaluate_ambiguity({"project_root": str(case_root), "prompt": prompt})
        is_ambiguous = result["ambiguous"] is True
        should_be_ambiguous = expected == "ambiguous"

        if is_ambiguous and not should_be_ambiguous:
            false_positives.append(prompt)
        if should_be_ambiguous and not is_ambiguous:
            false_negatives.append(prompt)

    assert false_positives == []
    assert len(false_negatives) <= 2, false_negatives


def test_known_corpus_false_negatives_except_question_form_now_score_at_least_two(tmp_path: Path) -> None:
    prompts = [
        "저번에 말한 거 해줘",
        "뭔가 이상해 수정해줘",
        "여기 에러나는거 니가 판단해서 고쳐줘",
        "저거 작동 안하는데 고쳐",
        "이전에 에러났던거 알아서 처리해줘",
        "좀 더 깔끔하게 바꿔줘",
    ]

    for prompt in prompts:
        result = evaluate_ambiguity({"project_root": str(tmp_path / prompt[:2]), "prompt": prompt})
        score = result["ambiguity_score"]

        assert isinstance(score, int)
        assert score >= 2, prompt
        assert result["ambiguous"] is True, prompt
