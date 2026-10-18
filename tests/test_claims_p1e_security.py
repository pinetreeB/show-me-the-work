"""Synthetic credentials only; never read local credential/config files."""
import base64

import pytest

from smtw.claims.packets import redact, sensitive_path


@pytest.mark.parametrize("key", [
    "password", "passwd", "pwd", "DB_PASSWORD", "db_password",
    "experimental_bearer_token", "api_key", "api-key", "credential",
    "private_key", "access-key", "aws_secret_access_key", "비밀번호",
])
@pytest.mark.parametrize("template", [
    '{key}=synth,tail', '"{key}": "synth,tail"', "{key}: 'synth,tail'",
    "{key}%3Dsynth%2Ctail", "{key}:\nsynth,tail",
])
def test_assignment_values_are_fully_removed(key, template):
    safe, counts = redact(template.format(key=key))
    assert "synth" not in safe and "tail" not in safe
    assert counts
    assert redact(safe) == (safe, {})


@pytest.mark.parametrize("prefix", [
    "sk-", "SK-", "github_pat_", "gho_", "ghu_", "ghs_", "ghr_",
    "xoxb-", "xoxa-", "xoxp-", "AIza", "Bearer ",
])
def test_provider_credentials(prefix):
    value = "SyntheticOnly0123456789"
    safe, counts = redact(prefix + value)
    assert value not in safe and counts


@pytest.mark.parametrize("separator", ["-", ".", " ", "  ", "–", "_", "/", "\u00a0", " - "])
@pytest.mark.parametrize("prefix,middle,tail", [
    ("010", "1234", "5678"), ("02", "123", "4567"), ("080", "123", "4567"),
    ("900101", "", "1234567"),
])
def test_personal_data_separators(separator, prefix, middle, tail):
    value = separator.join([prefix, middle, tail] if middle else [prefix, tail])
    safe, counts = redact("연락처" + value)
    assert tail not in safe and counts


@pytest.mark.parametrize("value", [
    "０１０-１２３４-５６７８", "(010) 1234-5678", "+82 (0)10 1234 5678",
    "1588-1234", "010-1234-56789", "a (at) b.com", "a%40b.com",
    '"a b"@b.com', "a@한글.한국", "a@b。\ncom",
])
def test_personal_data_variants(value):
    safe, counts = redact(value)
    assert value not in safe and counts


@pytest.mark.parametrize("value", [
    "0123456789abcdef" * 4, "aA1/" * 11,
    base64.b64encode(b"sk-SyntheticOnly0123456789").decode(),
    "-----BEGIN PRIVATE KEY-----\nSYNTHETIC-ONLY\n-----END PRIVATE KEY-----",
])
def test_opaque_or_encoded_credentials(value):
    safe, counts = redact("value=" + value)
    assert value not in safe and counts


@pytest.mark.parametrize("length", [7, 32, 40])
@pytest.mark.parametrize("context", ["{}", "commit {}", "HEAD: {}", "HEAD = '{}'", "commit `{}`"])
def test_git_hash_context_is_not_a_credential(length, context):
    sha = ("abcdef" * 7)[:length]
    text = context.format(sha)
    assert redact(text) == (text, {})
    safe, counts = redact("secret_token=" + sha)
    assert sha not in safe and counts


@pytest.mark.parametrize("path", [
    "prod.env", "env.production", ".git-credentials", ".netrc", ".npmrc", ".pgpass",
    "a.jks", "app.keystore", "service-account-prod.json", "token-data.json",
    "private_key.txt", "infra.tfvars", "kubeconfig", ".codex/config.toml",
    r"C:\synthetic\.codex\config.toml", ".claude/settings.json",
    ".gemini/settings.json", "customers.csv", "contract-2026.pdf", "taxes.xlsx",
    "docs/legal-notes.md", "고객자료.xlsx", "계약자료.pdf", "세무자료.csv", "법무검토.md",
])
def test_sensitive_filename_and_cli_paths(path):
    assert sensitive_path(path)


@pytest.mark.parametrize("text", [
    "alpha.py:20 returned VALUE = 7", "The code is ordinary prose.",
    "commit " + "abcdef" * 6 + "abcd", "HEAD: " + "deadbeef" * 5,
    "version 1.2.3 has 20 lines", "a%20b is encoded spacing", "fullwidth ＡＢＣ",
])
def test_nonsecret_text_is_preserved(text):
    assert redact(text) == (text, {})

def test_redaction_marker_inside_credential_cannot_hide_the_remaining_value():
    safe, counts = redact('token="[REDACTED:token],synthetic-tail"')
    assert "synthetic-tail" not in safe and counts
    safe, counts = redact("token=[REDACTED:token],synthetic-tail")
    assert "synthetic-tail" not in safe and counts


def test_numeric_hash_segments_are_metadata_in_git_context():
    sha = ("01012345678abcde" * 2 + "0123456789")[:40]
    assert len(sha) == 40
    assert redact("commit " + sha) == ("commit " + sha, {})
