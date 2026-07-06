from __future__ import annotations

from argparse import Namespace


def _paths(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _target_note(target: str) -> list[str]:
    match target:
        case "claude":
            return [
                "- 대상: claude",
                "- 강도: 간결 규율. Claude Code 훅이 일부 하드 게이트를 수행합니다.",
            ]
        case "codex":
            return [
                "- 대상: codex",
                "- 강도: 상세 규율. 훅이 없거나 부분 적용될 수 있으므로 지시와 사후 check를 모두 따릅니다.",
            ]
        case "agy":
            return [
                "- 대상: agy",
                "- 강도: 상세 규율. 훅 없는 CLI 산출물로 간주하며 완료 후 오케스트레이터 check가 게이트입니다.",
            ]
        case unreachable:
            raise AssertionError(f"unknown target: {unreachable}")


def run_brief(args: Namespace) -> int:
    paths = _paths(str(args.paths))
    verify_cmd = str(args.verify_cmd)
    sentinel = str(args.sentinel)
    lines = [
        "[fable-lite 위임 규율]",
        *_target_note(str(args.target)),
        "",
        "allowed_paths:",
        *[f"- {path}" for path in paths],
        "",
        "검증 요구:",
        f"- 반드시 실행하고 결과를 보고하세요: `{verify_cmd}`",
        "- 실패하면 수정 후 같은 검증을 다시 실행하고 최종 evidence를 남기세요.",
        "",
        "sentinel 규칙:",
        f"- 완료 후 빈 파일 `{sentinel}`를 생성하세요.",
        "- sentinel은 완료 감지 신호일 뿐이며, 검증 증거를 대체하지 않습니다.",
        "",
        "사후 check 예고:",
        "- 완료 후 오케스트레이터가 `python -m fable_lite check --root <project> --agent <agent> --since-file <marker>`를 실행합니다.",
        "- check가 RED이면 완료로 인정되지 않습니다.",
    ]
    print("\n".join(lines))
    return 0

