import copy
import json

from scripts.compare_claim_packets import compare


def packet(folder, name, items):
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps({"version": 1, "data_block": items}), encoding="utf-8")


def item(identifier, texts):
    return {"id": identifier, "excerpts": [
        {"path": "module.py", "source": "read", "text": text, "ref": i + 1,
         "version": i, "revision": "old-revision", "observation": i}
        for i, text in enumerate(texts)]}


def test_shuffle_rebatch_ref_and_provenance_are_separate_from_content(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    a, b = item("a-1", ["first", "second"]), item("a-2", ["third"])
    packet(left, "packet-1.json", [a, b])
    shuffled = copy.deepcopy(a)
    shuffled["excerpts"].reverse()
    for i, excerpt in enumerate(shuffled["excerpts"]):
        excerpt.update(ref=i + 1, version=i + 9, revision="new-revision", observation=i + 9)
    packet(right, "packet-2.json", [b])
    packet(right, "packet-9.json", [shuffled])
    result = compare(left, right)
    assert result["same_content"]
    assert result["metadata_changed_ids"] == ["a-1"]


def test_text_change_and_item_add_remove_are_not_hidden_by_normalization(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    packet(left, "packet-1.json", [item("shared", ["original"]), item("removed", ["gone"])])
    packet(right, "packet-7.json", [item("shared", ["changed"]), item("added", ["new"])])
    result = compare(left, right)
    assert not result["same_content"]
    assert result["changed_ids"] == ["shared"]
    assert result["added_ids"] == ["added"]
    assert result["removed_ids"] == ["removed"]
