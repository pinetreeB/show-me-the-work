import pytest

from smtw.claims.packets import redact
H40 = 'd4faa02' + '1b2c3d4e5f60718293a4b5c6d7e8f901a'   # 40 hex
H64 = ('a1b2c3d4' * 8)
tests = [
 ('commit keyword', f'commit {H40}'), ('HEAD keyword', f'HEAD {H40}'), ('HEAD backtick', f'HEAD `{H40}`'), ('bare line', H40),
 ('inline mid-sentence', f'기준은 {H40} 이다'), ('inline backtick', f'기준 `{H40}` 이다'), ('table cell', f'| 기준 | `{H40}` |'),
 ('range a..b', f'git log {H40}..{H40[:39]}0'), ('url blob', f'https://github.com/o/r/blob/{H40}/a.py'), ('@ hash', f'branch @ {H40}'),
 ('short hash', 'd4faa02'), ('12-hex', 'd4faa021b2c3'), ('sha256', f'sha256 {H64}'), ('SHA-256 colon', f'SHA256: {H64}'),
 ('uuid', '123e4567-e89b-12d3-a456-426614174000'), ('line range', 'engine.py:152-160, 2449-2455'), ('date', '2026-10-19 10:30:45'), ('date compact', '20261019'),
 ('epoch ms', '1760000000000'), ('ip', '192.168.0.1'), ('version', 'v1.2.3'), ('long camel id', 'localAccountClearPromiseHandlerFactoryRegistryEntry = x()'),
 ('long path alnum', 'docs/review/results/synthetic/packets/scan/audit/final'), ('type decl token', 'ownerToken: string'), ('max_tokens', 'max_tokens: 4096'),
 ('tokenizer', 'tokenizer = AutoTokenizer.from_pretrained(name)'), ('password policy prose', 'password: required, min 8 chars'), ('const', 'const SECRET_KEY_LENGTH = 32'),
 ('bearer prose', 'Bearer authentication scheme is used'), ('korean prose token', '토큰: 사용하지 않음'), ('count', '3,000 requests 010 errors'), ('phone-like version', '02.1234.5678'),
 ('id like', 'a-f8a0148303027486'), ('voucher', 'PINV-2026-0042'), ('backup name', 'dist/index.js.bak-5306b34-20261013'),
]

@pytest.mark.parametrize("name,text", tests, ids=[row[0] for row in tests])
def test_overredaction_controls(name, text):
    safe, counts = redact(text)
    if name == "phone-like version":
        # Without a version label this is a real Korean telephone form; keep
        # the positive privacy control, not a case-name-based product exception.
        assert safe == "[REDACTED:phone]" and counts == {"phone": 1}
    else:
        assert (safe, counts) == (text, {})


@pytest.mark.parametrize("text", [
    "password: str", "ownerToken: string", "max_tokens: 4096",
    "const SECRET_KEY_LENGTH = 32", "connect(password=password)",
    "tokenizer = AutoTokenizer.from_pretrained(name)",
    "client_secret = requestToken", "access_token = request_token",
    "request_handler_registry_configuration_factory",
])
def test_types_limits_and_code_references_are_not_credentials(text):
    assert redact(text) == (text, {})


@pytest.mark.parametrize("key", ["password", "token", "client_secret", "auth", "api"])
@pytest.mark.parametrize("value", ["string", "4096", "requestToken", "password"])
def test_quoted_literals_stay_secret_even_when_their_text_is_a_safe_code_reference(key, value):
    text = f'{key}="{value}"'
    safe, counts = redact(text)
    assert (safe, counts) == ("[REDACTED:password]", {"password": 1})


@pytest.mark.parametrize("length", [40, 64])
def test_secret_key_overrides_hash_context(length):
    value = ("abcdef0123456789" * 4)[:length]
    safe, counts = redact(f'sha256 secret_token = "{value}"')
    assert value not in safe and counts


@pytest.mark.parametrize("template", [
    "SHA-256: {}", "digest {}", "checksum={}", "manifest.json | {}",
    "{}  module.py", "module.py: {}", "{}.json",
])
def test_sha256_metadata_context(template):
    text = template.format("abcdef0123456789" * 4)
    assert redact(text) == (text, {})


@pytest.mark.parametrize("template", [
    "https://service.example/?sig={}", "https://service.example/?key={}",
    "https://{}@service.example/path", 'token="{}"',
])
def test_hash_inside_credential_location_is_not_metadata(template):
    value = "abcdef01" * 5
    safe, counts = redact(template.format(value))
    assert value not in safe and counts


def test_unquoted_numeric_password_is_not_a_limit():
    assert redact("password=4096") == ("[REDACTED:password]", {"password": 1})

def test_redaction_does_not_expand_the_source_window_past_its_original_budget():
    from tests.test_claims_packets import candidate, packetize, source
    content = 'password="' + "SyntheticOnly" * 200 + '"\nNEXT_ROW = 7\n'
    packets, summary = packetize([candidate()], [source(content)])
    assert summary["sent_count"] == 1
    text = packets[0]["data_block"][0]["excerpts"][0]["text"]
    assert text == "1: [REDACTED:password]"
