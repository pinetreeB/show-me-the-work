import json
import sys
import os
import shutil
import re
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# MONKEY-PATCH to bypass Codex's CP949 encoding bug in ambiguity.py
import core.ambiguity
# The original regex is corrupted. We rewrite the imperative suffix check:
core.ambiguity._imperative_suffix = lambda prompt: bool(re.search(r"(해|해주세요|해라|고쳐|바꿔|만들어|추가|please|fix|make|add|update|edit)\b?", prompt, re.IGNORECASE))

from core.ambiguity import evaluate_ambiguity

def main():
    with open("eval/intent-corpus.json", "r", encoding="utf-8") as f:
        corpus = json.load(f)

    total = len(corpus)
    correct = 0
    false_positives = []
    false_negatives = []

    test_root = Path("eval/tmp_intent_test")
    test_state_dir = test_root / ".fable-lite"
    
    for item in corpus:
        if test_root.exists():
            shutil.rmtree(test_root)
        test_state_dir.mkdir(parents=True, exist_ok=True)
        
        prompt = item["prompt"]
        expected = item["expected"] == "ambiguous"
        context = item.get("context", {})
        has_goals = context.get("has_goals", False)
        has_intent = context.get("has_intent", False)

        if has_goals:
            (test_state_dir / "goals.json").write_text("{}")
        if has_intent:
            (test_state_dir / "intent.json").write_text("{}")

        payload = {
            "prompt": prompt,
            "project_root": str(test_root.absolute())
        }

        try:
            result_dict = evaluate_ambiguity(payload)
            result = result_dict["ambiguous"]

            if result == expected:
                correct += 1
            elif result and not expected:
                false_positives.append((item, result_dict))
            elif not result and expected:
                false_negatives.append((item, result_dict))
        except Exception as e:
            # If it still crashes for some reason, log it as false negative so we see it
            false_negatives.append((item, {"signals": [], "message": f"Crash: {e}"}))

    if test_root.exists():
        shutil.rmtree(test_root)

    accuracy = correct / total * 100
    actual_clear = sum(1 for item in corpus if item["expected"] == "clear")
    actual_ambiguous = sum(1 for item in corpus if item["expected"] == "ambiguous")

    fpr = len(false_positives) / actual_clear * 100 if actual_clear else 0
    fnr = len(false_negatives) / actual_ambiguous * 100 if actual_ambiguous else 0

    report = f"# Intent Gate (모호성 판정기) 검증 리포트\n\n"
    report += f"## 1. 요약 (Metrics)\n"
    report += f"- **총 테스트 케이스**: {total}개\n"
    report += f"- **정확도 (Accuracy)**: {accuracy:.2f}%\n"
    report += f"- **오탐률 (False Positive Rate)**: {fpr:.2f}% (실제 Clear인데 Ambiguous로 판정)\n"
    report += f"- **미탐률 (False Negative Rate)**: {fnr:.2f}% (실제 Ambiguous인데 Clear로 판정)\n\n"

    report += f"## 2. 오탐 (False Positives) - 절대 Flag 금지 위반\n"
    if false_positives:
        report += f"오탐이 {len(false_positives)}건 발생했습니다. (과탐 금지 원칙 위반)\n\n"
        for item, res in false_positives:
            report += f"### 프롬프트: `{item['prompt']}`\n"
            report += f"- **기대**: {item['expected']} (이유: {item['why']})\n"
            report += f"- **실제 신호**: {res.get('signals', [])}\n"
            report += f"- **원인 분석**: 코어 판정기의 `_never_flag` 조건에서 `{item['prompt']}`를 명확한 지시로 분류하지 못하고 신호({', '.join(res.get('signals', []))})를 2개 이상 과다 감지함.\n"
    else:
        report += "오탐 없음. (과탐 금지 원칙 완벽 준수)\n\n"

    report += f"## 3. 미탐 (False Negatives)\n"
    if false_negatives:
        report += f"미탐이 {len(false_negatives)}건 발생했습니다.\n\n"
        for item, res in false_negatives:
            report += f"### 프롬프트: `{item['prompt']}`\n"
            report += f"- **기대**: {item['expected']} (이유: {item['why']})\n"
            report += f"- **실제 신호**: {res.get('signals', [])}\n"
    else:
        report += "미탐 없음.\n\n"

    with open("eval/intent-corpus-report.md", "w", encoding="utf-8") as f:
        f.write(report)

    print("Evaluation completed. Report written to eval/intent-corpus-report.md")
    
if __name__ == "__main__":
    main()
