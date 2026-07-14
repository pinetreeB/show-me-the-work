from __future__ import annotations

from core.classify import classify_prompt


def _paths(prompt: str) -> list[str]:
    result = classify_prompt({"prompt": prompt})
    paths = result["requested_paths"]
    assert isinstance(paths, list)
    assert all(isinstance(path, str) for path in paths)
    return paths


def _packs(prompt: str) -> tuple[str, list[str]]:
    result = classify_prompt({"prompt": prompt})
    mode = result["mode"]
    packs = result["packs"]
    assert isinstance(mode, str)
    assert isinstance(packs, list)
    assert all(isinstance(pack, str) for pack in packs)
    return mode, packs


def test_bare_framework_names_are_not_requested_paths() -> None:
    # Given/When: bare technology names appear without a file-boundary signal.
    results = {
        prompt: _paths(prompt)
        for prompt in (
            "next.js 설정을 바꿔줘",
            "NODE.JS 런타임을 설명해줘",
            "vue.js 설정을 바꿔줘",
        )
    }

    # Then: none of those framework names narrows the requested file scope.
    assert results == {
        "next.js 설정을 바꿔줘": [],
        "NODE.JS 런타임을 설명해줘": [],
        "vue.js 설정을 바꿔줘": [],
    }


def test_framework_names_with_file_boundaries_remain_requested_paths() -> None:
    # Given/When: the same dotted names have explicit path or file context.
    results = {
        "backticks": _paths("`next.js` 파일을 수정해줘"),
        "slash": _paths("src/node.js 고쳐줘"),
        "backslash": _paths(r"config\vue.js 고쳐줘"),
        "file_word": _paths("vue.js 파일 수정해줘"),
        "english_after": _paths("next.js file 수정해줘"),
        "english_before": _paths("file node.js 수정해줘"),
        "english_punctuation": _paths("file: vue.js 수정해줘"),
    }

    # Then: explicit file boundaries preserve the requested paths.
    assert results == {
        "backticks": ["next.js"],
        "slash": ["src/node.js"],
        "backslash": [r"config\vue.js"],
        "file_word": ["vue.js"],
        "english_after": ["next.js"],
        "english_before": ["node.js"],
        "english_punctuation": ["vue.js"],
    }


def test_real_filename_context_remains_a_requested_path() -> None:
    # Given/When/Then: a non-framework JavaScript filename stays file-like.
    assert _paths("app.js 고쳐줘") == ["app.js"]


def test_generation_explanation_is_quick_without_artifact_grounding() -> None:
    # Given/When: generation is discussed without a rendered artifact target.
    results = {
        prompt: _packs(prompt)
        for prompt in (
            "문서 생성 원리를 설명해줘",
            "생성형 AI가 뭐야?",
            "난수 생성 원리를 설명해줘",
            "generate a happy explanation",
            "generate configurable behavior explanations",
            "component generator architecture",
            "createdAt component field explanation",
        )
    }

    # Then: the explanation remains quick and needs no artifact observation pack.
    assert all(mode == "quick" for mode, _ in results.values())
    assert all("verification-grounding" not in packs for _, packs in results.values())


def test_observable_generation_targets_keep_artifact_grounding() -> None:
    # Given/When: imperative generation names an observable target or output file.
    prompts = (
        "HTML 페이지 생성해줘",
        "UI 생성해줘",
        "game 생성해줘",
        "chart 생성해줘",
        "이미지 생성해줘",
        "문서 생성해줘",
        "report.pdf 생성해줘",
        "app.py 생성해줘",
    )
    results = {prompt: _packs(prompt) for prompt in prompts}

    # Then: every concrete output request requires artifact observation.
    expected = {prompt: ("normal", True) for prompt in prompts}
    actual = {
        prompt: (mode, "verification-grounding" in packs)
        for prompt, (mode, packs) in results.items()
    }
    assert actual == expected


def test_korean_development_generation_targets_require_artifact_grounding() -> None:
    # Given/When: a bare framework name accompanies a concrete development output request.
    prompts = (
        "vue.js에 컴포넌트 파일 생성해줘",
        "next.js로 설정 파일 생성해줘",
        "vue.js 컴포넌트 생성해줘",
        "next.js 프로젝트 보일러플레이트 생성해줘",
    )
    results = {prompt: _packs(prompt) for prompt in prompts}

    # Then: output intent routes to artifact verification without treating the framework as a path.
    assert all(mode in {"normal", "deep"} for mode, _ in results.values())
    assert all("verification-grounding" in packs for _, packs in results.values())
    assert all(_paths(prompt) == [] for prompt in prompts)


def test_english_development_generation_targets_require_artifact_grounding() -> None:
    # Given/When: English create/generate verbs name concrete development outputs.
    prompts = ("create a next.js app", "generate a vue.js component")
    results = {prompt: _packs(prompt) for prompt in prompts}

    # Then: both requests enter the artifact-verification path.
    assert all(mode in {"normal", "deep"} for mode, _ in results.values())
    assert all("verification-grounding" in packs for _, packs in results.values())


def test_korean_nominal_generation_requests_require_artifact_grounding() -> None:
    # Given/When: concise Korean requests end with nominal request markers.
    prompts = ("pdf 문서 생성 필요", "보고서 생성 좀")
    results = {prompt: _packs(prompt) for prompt in prompts}

    # Then: the concrete targets keep these phrases out of quick mode.
    assert all(mode in {"normal", "deep"} for mode, _ in results.values())
    assert all("verification-grounding" in packs for _, packs in results.values())


def test_nominal_generation_language_does_not_break_briefing_suppression() -> None:
    # Given/When: a boot briefing describes a required generated sentinel and ends in standby.
    result = classify_prompt(
        {"prompt": "[부팅] sentinel 파일 생성 필요 조건을 읽고 파악한 뒤 대기하라"}
    )
    packs = result["packs"]

    # Then: briefing suppression still wins over creation-language detection.
    assert result["briefing"] is True
    assert result["needs_goals"] is False
    assert isinstance(packs, list)
    assert "verification-grounding" not in packs


def test_actual_wmux_boot_templates_are_briefings_without_provenance_authority() -> None:
    prompts = (
        "[부팅] 너는 wmux 4-pane 팀의 우상 pane, 역할=구현이다. MEMORY.md와 project.md를 읽고 상태를 파악한 뒤 대기하라.",
        "세션 부팅: 공통 메모리와 fable-lite/project.md를 읽어 운영규칙을 파악해라. 완료 후 부팅 완료만 보고하고 대기해라.",
        "Role briefing: inspect MEMORY.md and the project note, report READY-CODEX only, then wait.",
    )

    results = [classify_prompt({"prompt": prompt}) for prompt in prompts]

    assert all(result["briefing"] is True for result in results)
    assert all(result["needs_goals"] is False for result in results)


def test_boot_marker_cannot_disguise_real_write_instruction() -> None:
    result = classify_prompt(
        {
            "prompt": (
                "[부팅] MEMORY.md를 읽고 운영규칙을 파악한 뒤 "
                "app.py를 실제로 고쳐줘. 마지막에는 대기하라"
            )
        }
    )

    assert result["briefing"] is False
