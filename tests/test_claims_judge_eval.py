import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from smtw.claims.advisory import load_sent_items,verify_judgments
from smtw.claims.judge_eval import generate,score,score_table,summarize
from smtw.claims import judge
from tests.claims_judge_support import make_scope


@pytest.fixture
def scope_file(tmp_path):
    return make_scope(tmp_path)


@pytest.fixture(autouse=True)
def no_real_launcher(monkeypatch):
    def fail(*args,**kw):pytest.fail("REAL MODEL EXECUTION FORBIDDEN")
    monkeypatch.setattr(judge,"_launch",fail)
    monkeypatch.setattr(judge.subprocess,"run",fail)


def pool():
    rows = []
    for index in range(100):
        quote = f"MAX_LIMIT = 7; fixture_index = {index}"
        category = "code" if index % 2 else "document"
        rows.append({"case":"example-case","path":"~/example-repo/sample.py",
                     "line":1,"quote":quote,"window":{1:quote,2:"MIN_LIMIT = 1"},
                     "text":"1: " + quote + "\n2: MIN_LIMIT = 1","redactions":{},
                     "report_line":1,"observation":"synthetic", "category":category,
                     "source":"git_show", "revision":"a" * 40})
    return rows


def make_run(path,sent,truth,*,wrong=False,bad_quote=False):
    path.mkdir()
    rows=[]
    for index,(key,value) in enumerate(truth.items()):
        verdict=value["verdict"]
        if wrong and index==0:verdict="contradicted" if verdict=="supported" else "supported"
        rows.append({"id":key,"verdict":verdict,"rationale":"Synthetic oracle fixture.",
                     "evidence_ref":1,"evidence_quote":"made up quote" if bad_quote and index==0 else "MAX_LIMIT = 7"})
    (path/"judgments-001.json").write_text(json.dumps(rows))
    (path/"verified-001.json").write_text(json.dumps(verify_judgments(rows,sent)))
    return path


def test_200_paired_items_truth_separation_and_no_answer_hints(tmp_path, scope_file):
    result=generate(pool(),tmp_path/"eval",scope_file=scope_file)
    assert result["items"]==200 and result["unique_originals"]>=60 and result["model_calls"]==0
    folder=tmp_path/"eval/packets";sent=load_sent_items(folder)
    truth=json.loads((tmp_path/"eval/truth/answers.json").read_text(encoding="utf-8"))
    assert sum(v["verdict"]=="supported" for v in truth["items"].values())==100
    assert sum(v["verdict"]=="contradicted" for v in truth["items"].values())==100
    assert set(truth["items"])==set(sent)
    assert truth["unique_used_quotes"] >= 60
    assert set(truth["originals_by_category"]) == {"code", "document"}
    assert all(item["excerpts"][0]["source"] == "git_show" for item in sent.values())
    assert all(item["excerpts"][0]["revision"] == "a" * 40 for item in sent.values())
    forbidden={"truth","expected","verdict","mutation","is_original","answers","sampling"}
    def walk(value):
        if isinstance(value,dict):
            assert not forbidden.intersection(value)
            for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    for file in folder.glob("*.json"):walk(json.loads(file.read_text(encoding="utf-8")))
    assert {p.name for p in folder.iterdir()}=={"manifest.json",*[p.name for p in folder.glob("packet-*.json")]}
    assert "| Packet | Items |" in summarize(folder,scope_file=scope_file)


def test_outside_case_and_source_rejected(tmp_path, scope_file):
    for field,value in [("case","unapproved-case"),("path","~/unapproved-repo/task.py")]:
        rows=pool();rows[0][field]=value
        with pytest.raises(ValueError):generate(rows,tmp_path/field,scope_file=scope_file)


def test_scores_and_three_run_agreement_are_from_verified_outputs(tmp_path, scope_file):
    generate(pool(),tmp_path/"eval",scope_file=scope_file,per_kind=1)
    folder=tmp_path/"eval/packets";sent=load_sent_items(folder)
    truth_file=tmp_path/"eval/truth/answers.json";truth=json.loads(truth_file.read_text(encoding="utf-8"))["items"]
    runs=[make_run(tmp_path/f"r{i}",sent,truth,wrong=i==2) for i in range(3)]
    result=score(folder,truth_file,runs,scope_file=scope_file)
    assert result["runs"][0]["recall"]==1 and result["runs"][0]["precision"]==1
    assert result["runs"][2]["false_positive"]==1
    assert result["runs"][2]["false_display_rate"]==0.2
    assert result["verdict_agreement_3"]==0.9
    assert result["verified_quote_agreement_3"]==1
    assert score_table(result).splitlines()[0]=="| Run | Recall | Precision | False display | Quote not in excerpt | Unjudged |"


def test_bad_quote_not_displayed_and_forged_verifier_rejected(tmp_path, scope_file):
    generate(pool(),tmp_path/"eval",scope_file=scope_file,per_kind=1)
    folder=tmp_path/"eval/packets";sent=load_sent_items(folder)
    truth_file=tmp_path/"eval/truth/answers.json";truth=json.loads(truth_file.read_text(encoding="utf-8"))["items"]
    run=make_run(tmp_path/"run",sent,truth,bad_quote=True)
    assert score(folder,truth_file,[run],scope_file=scope_file)["runs"][0]["quote_not_in_excerpt_rate"]==0.1
    path=run/"verified-001.json";value=json.loads(path.read_text(encoding="utf-8"));value["counts"]["quote_verified"]+=1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):score(folder,truth_file,[run],scope_file=scope_file)


def test_all_wrapper_calls_in_pipeline_use_injected_fake(tmp_path, scope_file):
    generate(pool(),tmp_path/"eval",scope_file=scope_file,per_kind=1)
    calls=[]
    def fake(argv,prompt,env):
        calls.append(argv)
        data=json.loads(prompt.split("PACKET DATA\n",1)[1])
        rows=[{"id":item["id"],"verdict":"not_enough_evidence","rationale":"Fake pipeline only."}
              for item in data["data_block"]]
        return SimpleNamespace(returncode=0,stdout=json.dumps({"subtype":"success","is_error":False,"api_error_status":None,"structured_output":{"judgments":rows}}))
    result=judge.judge(tmp_path/"eval/packets",executor=fake,tty=lambda:True,
        parent_env={},usage_reader=lambda:{"known":True,"five_hour":0,"weekly":0},
        executable="claude-p",output_root=tmp_path/"calls",scope_file=scope_file)
    assert len(calls)==result["calls"] and result["execution"]=="injected"


def test_repeated_text_cannot_inflate_original_diversity(tmp_path, scope_file):
    with pytest.raises(ValueError, match="distinct original"):
        generate([pool()[0]] * 100, tmp_path / "repeated", scope_file=scope_file)
    assert not (tmp_path / "repeated/packets").exists()


def test_fixed_repo_pool_filters_paths_before_read_and_lines_after_read(tmp_path, scope_file, monkeypatch):
    from smtw.claims import judge_eval as evaluation
    calls = []
    class FixedRepository:
        def __init__(self, roots, commits, aliases, budget):
            self.commits = commits
        def files(self, root, revision):
            assert self.commits[str(root)] == revision
            return [".codex/config.toml", "docs/credentials.txt", "eval/pilot/hidden.md",
                    "docs/fake-fixture.md", "docs/guide.md", "smtw/module.py"]
        def git(self, root, operation, object_id):
            revision, name = object_id.split(":", 1)
            assert operation == "show" and self.commits[str(root)] == revision
            assert name in {"docs/guide.md", "smtw/module.py"}
            calls.append((str(root), object_id))
            prefix = "doc" if name.endswith(".md") else "code"
            rows = ["short", "password=synthetic_example_4821",
                     "the source at ~/.codex/config.toml must never enter the corpus",
                     "padding statement for synthetic corpus safety",
                     "padding statement for synthetic corpus safety"]
            rows += [f"{prefix} {root.name} original source line number {n}" for n in range(80)]
            return SimpleNamespace(returncode=0, stdout="\n".join(rows).encode("utf-8"))
    monkeypatch.setattr(evaluation, "Repositories", FixedRepository)
    rows, receipts = evaluation.collect_pool(scope_file)
    assert len({r["quote"] for r in rows}) >= 60
    assert {r["category"] for r in rows} == {"code", "document"}
    assert {r["case"] for r in rows} == {"example-case", "other-case"}
    assert len(calls) == 4
    assert {r["revision"] for r in rows} == {v["revision"] for v in receipts.values()}
    assert all(len(r["quote"]) >= 20 and not r["redactions"] for r in rows)
    assert all("synthetic_example" not in r["text"] and ".codex" not in r["text"] for r in rows)
    assert len({r["quote"] for r in rows}) == len(rows)



def assert_no_answer_hints(folder):
    forbidden = {"truth", "expected", "verdict", "mutation", "is_original", "answers", "sampling"}
    def walk(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for file in folder.glob("*.json"):
        walk(json.loads(file.read_text(encoding="utf-8")))


@pytest.mark.parametrize("batch_size", [1, 2, 99])
def test_packet_batches_validate_independently_and_preserve_all_items(tmp_path, scope_file, batch_size):
    from collections import Counter
    whole = tmp_path / "whole"
    split = tmp_path / "split"
    original = generate(pool(), whole, scope_file=scope_file)
    result = generate(pool(), split, scope_file=scope_file, batch_size=batch_size)
    expected = load_sent_items(whole / "packets")
    expected_truth = json.loads((whole / "truth/answers.json").read_text(encoding="utf-8"))["items"]
    combined, truth_items, packet_bytes = {}, {}, []
    batches = sorted((split / "packets").iterdir())
    assert len(batches) == (original["packets"] + batch_size - 1) // batch_size
    assert [p.name for p in batches] == [f"batch-{i:03d}" for i in range(1, len(batches) + 1)]
    assert len(result["batches"]) == len(batches)
    assert not (split / "packets/manifest.json").exists()
    for folder, summary in zip(batches, result["batches"]):
        sent = load_sent_items(folder)  # Existing judge SHA/size/id validation.
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        answers = split / "truth" / folder.name / "answers.json"
        truth = json.loads(answers.read_text(encoding="utf-8"))
        assert set(sent) == set(truth["items"]) == set(manifest["item_cases"])
        assert 1 <= len(manifest["packets"]) <= batch_size
        assert manifest["sent_count"] == manifest["selected_count"] == len(sent)
        assert manifest["deferred_count"] == 0
        assert summary["packets_dir"] == str(folder) and summary["answers"] == str(answers)
        assert truth["original_count"] == sum(v["verdict"] == "supported" for v in truth["items"].values())
        assert truth["mutation_count"] == sum(v["verdict"] == "contradicted" for v in truth["items"].values())
        assert not set(combined).intersection(sent)
        assert "| Packet | Items |" in summarize(folder, scope_file=scope_file)
        assert_no_answer_hints(folder)
        combined.update(sent)
        truth_items.update(truth["items"])
        packet_bytes.extend(p.read_bytes() for p in folder.glob("packet-*.json"))
    assert combined == expected and truth_items == expected_truth
    assert Counter(packet_bytes) == Counter(p.read_bytes() for p in (whole / "packets").glob("packet-*.json"))
    assert result["items"] == original["items"] == 200 and result["model_calls"] == 0
    # A changed packet invalidates only its own folder's hash set.
    first = next(batches[0].glob("packet-*.json"))
    first.write_bytes(first.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA"):
        load_sent_items(batches[0])
    for folder in batches[1:]:
        assert load_sent_items(folder)


@pytest.mark.parametrize("limit", [2, 12, 44, 200, 202])
def test_item_limit_selects_first_complete_pairs_before_shuffle(tmp_path, scope_file, limit):
    generate(pool(), tmp_path / "whole", scope_file=scope_file)
    original = json.loads((tmp_path / "whole/truth/answers.json").read_text(encoding="utf-8"))
    result = generate(pool(), tmp_path / "small", scope_file=scope_file, limit_items=limit)
    sent = load_sent_items(tmp_path / "small/packets")
    truth = json.loads((tmp_path / "small/truth/answers.json").read_text(encoding="utf-8"))
    expected_ids = list(original["items"])[:limit]
    assert set(sent) == set(truth["items"]) == set(expected_ids)
    assert truth["items"] == {key: original["items"][key] for key in expected_ids}
    assert truth["original_count"] == truth["mutation_count"] == min(limit, 200) // 2
    for i in range(0, len(expected_ids), 2):
        pair = [original["items"][key]["verdict"] for key in expected_ids[i:i+2]]
        assert pair == ["supported", "contradicted"]
    assert sum(truth["originals_by_category"].values()) == min(limit, 200) // 2
    assert result["items"] == min(limit, 200)
    assert result["unique_originals"] == truth["unique_used_quotes"] == min(limit, 200) // 2
    assert_no_answer_hints(tmp_path / "small/packets")


@pytest.mark.parametrize("option,value", [
    ("batch_size", 0), ("batch_size", -1), ("batch_size", True),
    ("limit_items", 0), ("limit_items", -2), ("limit_items", 1), ("limit_items", 3),
])
def test_generation_bounds_reject_invalid_values_without_writes(tmp_path, scope_file, option, value):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="batch-size|limit-items"):
        generate(pool(), output, scope_file=scope_file, **{option: value})
    assert not output.exists()


def test_generate_cli_limits_and_batches_with_folder_scoring(tmp_path, scope_file, monkeypatch, capsys):
    from smtw.claims import judge_eval as evaluation
    monkeypatch.setattr(evaluation, "collect_pool", lambda path: (pool(), {}))
    output = tmp_path / "cli"
    assert evaluation.main(["generate", "--scope-file", str(scope_file), "--out-root", str(output),
                            "--limit-items", "12", "--batch-size", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["items"] == 12 and len(result["batches"]) == 1
    folder = output / "packets/batch-001"
    truth_file = output / "truth/batch-001/answers.json"
    truth = json.loads(truth_file.read_text(encoding="utf-8"))["items"]
    sent = load_sent_items(folder)
    fake_run = make_run(tmp_path / "fake-run", sent, truth)
    scored = score(folder, truth_file, [fake_run], scope_file=scope_file)
    assert scored["runs"][0]["recall"] == scored["runs"][0]["precision"] == 1
    assert scored["runs"][0]["unjudged"] == 0 and scored["model_calls"] == 0


@pytest.mark.parametrize("option,value", [("--batch-size", "0"), ("--limit-items", "3")])
def test_cli_rejects_invalid_bounds_before_collecting_sources(tmp_path, scope_file, monkeypatch, option, value):
    from smtw.claims import judge_eval as evaluation
    def forbidden(*args):
        pytest.fail("invalid bounds must not read corpus sources")
    monkeypatch.setattr(evaluation, "collect_pool", forbidden)
    with pytest.raises(SystemExit) as error:
        evaluation.main(["generate", "--scope-file", str(scope_file), "--out-root", str(tmp_path / "bad"),
                         option, value])
    assert error.value.code == 2
    assert not (tmp_path / "bad").exists()



def realistic_pool():
    rows = []
    for case, prefix, revision in [("example-case", "example-repo", "a"),
                                   ("other-case", "other-repo", "b")]:
        for index in range(80):
            word = ("primary" if case == "example-case" else "secondary") + f"_setting_{index:03d}"
            quote = f"{word} = 7000000"
            rows.append({"case": case, "path": f"~/{prefix}/module-{index:03d}.py",
                         "line": 1, "quote": quote, "window": {1: quote, 2: "90000000"},
                         "text": f"1: {quote}\n2: 90000000", "redactions": {}, "report_line": 1,
                         "observation": "source-observation", "category": "code",
                         "source": "git_show", "revision": revision * 40})
    return rows


def quoted_claim(claim):
    import re
    match = re.search(r":(\d+) (does not contain|contains) the quotation ", claim)
    assert match
    quoted, _ = json.JSONDecoder().raw_decode(claim[match.end():])
    return int(match.group(1)), match.group(2) == "does not contain", quoted


def test_mutations_use_actual_repository_tokens_and_preserve_literal_truth(tmp_path, scope_file):
    import re
    rows = realistic_pool()
    result = generate(rows, tmp_path / "natural", scope_file=scope_file)
    folder = tmp_path / "natural/packets"
    sent = load_sent_items(folder)
    truth = json.loads((tmp_path / "natural/truth/answers.json").read_text(encoding="utf-8"))["items"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert result["items"] == 200 and result["unique_originals"] >= 60
    originals = {r["path"]: r["quote"] for r in rows}
    words = {case: {r["quote"].split()[0] for r in rows if r["case"] == case}
             for case in ["example-case", "other-case"]}
    markers = re.compile(r"altered|tamper|fake|missing_\d", re.I)
    kinds = set()
    for key, item in sent.items():
        assert not markers.search(json.dumps(item))
        n, negated, quote = quoted_claim(item["claim"])
        lines = {int(m[1]): m[2] for line in item["excerpts"][0]["text"].splitlines()
                 if (m := re.match(r"(\d+): ?(.*)", line))}
        answer = truth[key]
        if answer["verdict"] == "supported":
            assert not negated and quote in lines[n]
            continue
        kind = answer["mutation"]
        kinds.add(kind)
        if kind == "negation":
            assert negated and quote in lines[n]
        else:
            assert not negated and quote not in lines[n]
        if kind in {"quote_token", "identifier", "number"}:
            assert all(quote not in line for line in lines.values())
        if kind in {"quote_token", "identifier"}:
            original = originals[item["excerpts"][0]["path"]]
            replacement = quote.split()[0]
            assert replacement != original.split()[0]
            assert replacement in words[manifest["item_cases"][key]]
    assert kinds == {"quote_token", "line_move", "identifier", "number", "negation"}
    assert_no_answer_hints(folder)


@pytest.mark.parametrize("number", ["0", "9", "10", "99", "100", "009"])
def test_number_mutations_are_adjacent_and_keep_width(number):
    import random,re
    from smtw.claims.judge_eval import mutate
    quote = "configured_limit = " + number
    row = {**pool()[0], "quote": quote, "window": {1: quote, 2: "Other setting is available."},
           "text": "1: " + quote + "\n2: Other setting is available."}
    for seed in range(8):
        mutated = mutate(row, "number", random.Random(seed))
        assert mutated is not None
        _, _, changed = quoted_claim(mutated)
        value = changed.split(" = ")[1]
        assert value.isdecimal() and len(value) == len(number)
        assert abs(int(value) - int(number)) == 1


def test_mutation_retries_observed_alternatives_instead_of_creating_a_false_label():
    import random
    from smtw.claims.judge_eval import mutate
    row = {**pool()[0], "quote": "primary_limit = 7",
           "window": {1: "primary_limit = 7", 2: "secondary_limit = 7", 3: "fallback_limit = 9"},
           "text": "1: primary_limit = 7\n2: secondary_limit = 7\n3: fallback_limit = 9"}
    for seed in range(8):
        altered = mutate(row, "identifier", random.Random(seed))
        assert quoted_claim(altered)[2] == "fallback_limit = 7"
    row["window"] = {1: "primary_limit = 7", 2: "primary_limit = 6", 3: "primary_limit = 8"}
    row["text"] = "\n".join(f"{n}: {value}" for n, value in row["window"].items())
    assert mutate(row, "number", random.Random(0)) is None


def test_marker_bearing_sources_do_not_enter_packets(tmp_path, scope_file):
    import re
    rows = pool()
    for marker in ["altered_token", "tamper_variant", "fake_marker"]:
        row = dict(rows[0])
        row["quote"] = f"{marker} = 7; another_setting = 2"
        row["window"] = {1: row["quote"], 2: "another_setting = 3"}
        row["text"] = "1: " + row["quote"] + "\n2: another_setting = 3"
        rows.append(row)
    generate(rows, tmp_path / "clean", scope_file=scope_file)
    for path in (tmp_path / "clean/packets").glob("*.json"):
        assert not re.search(r"altered|tamper|fake|missing_\d", path.read_text(encoding="utf-8"), re.I)
