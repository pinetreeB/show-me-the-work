"""r10: 30+ new synthetic bypass attempts against packets.redact / sensitive_path (all values synthetic markers)."""
import base64

import pytest
from smtw.claims.packets import redact, sensitive_path
H64 = 'c0ffee' * 10 + 'beef'      # 64 hex
H32 = 'ab12cd34' * 4
B64U = 'Zk9_aB-cD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2wX3yZ4'  # 43 chars base64url
cases = []
def c(group, name, text, secret, card=True):
    cases.append((group, name, text, secret, card))
# A. split / unusual key names
c('key', 'DB_PASS_WORD', 'DB_PASS_WORD=SECRETAAA1', 'SECRETAAA1', False)
c('key', 'pw short', 'pw=SECRETAAA2', 'SECRETAAA2', False)
c('key', 'pass short json', '{"pass": "SECRETAAA3"}', 'SECRETAAA3', False)
c('key', 'passphrase', 'passphrase: SECRETAAA4', 'SECRETAAA4', False)
c('key', 'passcode', 'passcode = "SECRETAAA5"', 'SECRETAAA5', False)
c('key', 'camel clientSecret', 'clientSecret: SECRETAAA6', 'SECRETAAA6')
c('key', 'camel apiKey', 'const apiKey = "SECRETAAA7"', 'SECRETAAA7')
c('key', 'x-api-key header', 'X-API-Key: SECRETAAA8', 'SECRETAAA8')
c('key', 'PRIVATE-TOKEN header', 'PRIVATE-TOKEN: SECRETAAA9', 'SECRETAAA9')
c('key', 'session cookie', 'Cookie: sessionid=SECRETAB10; csrftoken=SECRETAB11', 'SECRETAB10', False)
c('key', 'ssh_key assign', 'ssh_key = "SECRETAB12"', 'SECRETAB12', False)
c('key', 'auth= ', 'auth=SECRETAB13', 'SECRETAB13', False)
# B. multi-line blocks
c('block', 'toml section', '[secrets]\nprod = "SECRETBA1"\nstaging = "SECRETBA2"', 'SECRETBA1', False)
c('block', 'yaml nested tokens', 'tokens:\n  prod: SECRETBA3\n  dev: SECRETBA4', 'SECRETBA3')
c('block', 'yaml password block scalar', 'password: |\n  SECRETBA5\n', 'SECRETBA5')
c('block', 'yaml list', 'password:\n  - SECRETBA6\n', 'SECRETBA6')
c('block', 'toml multi-line string', 'api_key = """\nSECRETBA7\n"""', 'SECRETBA7')
c('block', 'unquoted multiword', 'password: SECRETBA8 horseBA9 staple', 'horseBA9')
c('block', 'dotenv export', 'export STRIPE_SECRET_KEY=SECRETBA10', 'SECRETBA10')
c('block', 'powershell env', '$env:OPENAI_API_KEY="SECRETBA11"', 'SECRETBA11')
# C. escaped / nested JSON
c('esc', 'escaped json', r'{\"password\": \"SECRETCA1\"}', 'SECRETCA1')
c('esc', 'double escaped', r'{\\"token\\": \\"SECRETCA2\\"}', 'SECRETCA2')
c('esc', 'json in string value', '{"body": "{\\"api_key\\":\\"SECRETCA3\\"}"}', 'SECRETCA3')
c('esc', 'escaped quote in value', r'password="SECRETCA4\"tail5"', 'tail5')
c('esc', 'unicode escape key', r'{"\u0070assword": "SECRETCA6"}', 'SECRETCA6', False)
c('esc', 'html entity quote', 'password=&quot;SECRETCA7&quot;', 'SECRETCA7')
c('esc', 'xml element', '<password>SECRETCA8</password>', 'SECRETCA8')
c('esc', 'xml attr', '<add key="ApiKey" value="SECRETCA9"/>', 'SECRETCA9', False)
c('esc', 'php arrow', "'password' => 'SECRETCA10'", 'SECRETCA10')
# D. Authorization headers
c('hdr', 'Basic short', 'Authorization: Basic dXNlcjpwYXNz', 'dXNlcjpwYXNz')
c('hdr', 'Token scheme', 'Authorization: Token SECRETDA1abc', 'SECRETDA1abc')
c('hdr', 'ApiKey scheme', 'Authorization: ApiKey SECRETDA2abc', 'SECRETDA2abc')
c('hdr', 'json header', '{"Authorization": "Bearer SECRETDA3abc"}', 'SECRETDA3abc')
c('hdr', 'curl -H', "curl -H 'Authorization: Bearer SECRETDA4abc' https://x", 'SECRETDA4abc')
c('hdr', 'space before colon', 'Authorization : Bearer SECRETDA5abc', 'SECRETDA5abc')
c('hdr', 'bearer newline', 'Authorization: Bearer\nSECRETDA6abc', 'SECRETDA6abc')
c('hdr', 'bearer tab', 'Bearer\tSECRETDA7abc', 'SECRETDA7abc')
c('hdr', 'Proxy-Authorization', 'Proxy-Authorization: Basic c2VjcmV0cHJveHk=', 'c2VjcmV0cHJveHk=')
c('hdr', 'digest', 'Authorization: Digest username="u", response="SECRETDA8abcdef"', 'SECRETDA8abcdef')
# E. URL query / userinfo
c('url', '?token=', 'https://h.example/a?token=SECRETEA1&x=1', 'SECRETEA1')
c('url', '?access_token=', 'GET /cb?access_token=SECRETEA2&state=1', 'SECRETEA2')
c('url', '#access_token', 'https://h/cb#access_token=SECRETEA3&t=bearer', 'SECRETEA3')
c('url', '?sig=', 'https://s.blob.example/c?sv=1&sig=SECRETEA4%2Bxyz', 'SECRETEA4', False)
c('url', '?key=', 'https://g.example/v1?key=SECRETEA5', 'SECRETEA5', False)
c('url', 'redis empty user', 'redis://:SECRETEA6@cache:6379/0', 'SECRETEA6')
c('url', 'userinfo no pw', 'https://SECRETEA7@host/path', 'SECRETEA7', False)
c('url', 'userinfo @ in pw', 'postgres://u:SECRETEA8%40x@db/x', 'SECRETEA8')
# F. CLI flags
c('cli', '--password space', 'mysql --password SECRETFA1 -u root', 'SECRETFA1', False)
c('cli', '--token space', 'gh auth login --token SECRETFA2abc', 'SECRETFA2abc', False)
c('cli', '-p glued', 'mysql -pSECRETFA3 db', 'SECRETFA3', False)
c('cli', 'curl -u', 'curl -u admin:SECRETFA4 https://x', 'SECRETFA4', False)
c('cli', '--password=', 'tool --password=SECRETFA5', 'SECRETFA5')
c('cli', 'natural language', 'the password is SECRETFA6', 'SECRETFA6', False)
c('cli', 'ko natural', '비밀번호는 SECRETFA7 입니다', 'SECRETFA7', False)
# G. opaque values
c('opaque', '64hex alone', 'value ' + H64 + ' end', H64)
c('opaque', '64hex upper', H64.upper(), H64.upper())
c('opaque', '0x64hex', '0x' + H64, H64)
c('opaque', '32hex', H32, H32)
c('opaque', '64hex colon bytes', ':'.join(H64[i:i+2] for i in range(0, 64, 2)), H64[:8])
c('opaque', '64hex wrapped 32/32', H64[:32] + '\n' + H64[32:], H64[:32])
c('opaque', '64hex split 31+33 space', H64[:31] + ' ' + H64[31:], H64[:20])
c('opaque', '48hex', 'c0ffee' * 8, 'c0ffee' * 8)
c('opaque', '40hex non-git (key)', 'api: ' + 'beadface' * 5, 'beadface' * 5)
c('opaque', 'base64url 43', 'x ' + B64U + ' y', B64U[:20])
c('opaque', 'base64 std 44', base64.b64encode(b'SECRETGA1' * 3 + b'padx').decode(), base64.b64encode(b'SECRETGA1' * 3 + b'padx').decode()[:30])
c('opaque', 'alnum 32 mixed', 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1', 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1', False)
c('opaque', 'alnum 36 mixed', 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2w', 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2w', False)
c('opaque', 'sk_live', 'sk_live_' + 'Zk9aBcD3eF4gH5iJ6kL7mN8o', 'Zk9aBcD3eF4gH5iJ6kL7mN8o', False)
c('opaque', 'glpat', 'glpat-' + 'Zk9aBcD3eF4gH5iJ6kL7', 'Zk9aBcD3eF4gH5iJ6kL7', False)
c('opaque', 'npm_', 'npm_' + 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2w', 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2w', False)
c('opaque', 'hf_', 'hf_' + 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV', 'Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV', False)
c('opaque', 'ya29', 'ya29.' + 'A0Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2wX3', 'A0Zk9aBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2wX3', False)
c('opaque', 'pgp private block', '-----BEGIN PGP PRIVATE KEY BLOCK-----\nSECRETGA2line\n-----END PGP PRIVATE KEY BLOCK-----', 'SECRETGA2line')
c('opaque', 'ssh private block', '-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETGA3line\n-----END OPENSSH PRIVATE KEY-----', 'SECRETGA3line')
c('opaque', 'sk with zero width', 'sk-\u200bZk9aBcD3eF4gH5iJ6kL7', 'Zk9aBcD3eF4gH5iJ6kL7')
c('opaque', 'sk with newline split', 'sk-Zk9aBcD3eF\n4gH5iJ6kL7mN8', '4gH5iJ6kL7mN8')
# H. Korean keys
c('ko', '토큰:', '토큰: SECRETHA1', 'SECRETHA1')
c('ko', '암호:', '암호: SECRETHA2', 'SECRETHA2')
c('ko', '암호=', '암호=SECRETHA3', 'SECRETHA3')
c('ko', '비밀번호는', '비밀번호는 SECRETHA4', 'SECRETHA4', False)
c('ko', '패스워드:', '패스워드: SECRETHA5', 'SECRETHA5')
c('ko', '비밀키:', '비밀키: SECRETHA6', 'SECRETHA6')
c('ko', '인증키:', '인증키: SECRETHA7', 'SECRETHA7')
c('ko', '시크릿:', '시크릿: SECRETHA8', 'SECRETHA8', False)
c('ko', '비번:', '비번: SECRETHA9', 'SECRETHA9', False)
c('ko', '토큰 json', '{"토큰": "SECRETHA10"}', 'SECRETHA10')
c('ko', '비밀번호 fullwidth colon', '비밀번호：SECRETHA11', 'SECRETHA11')
# I. phone/rrn/email variants
for nm, p in [('+1 (415) 555-2671', '+1 (415) 555-2671'), ('+82 010-1234-5678', '+82 010-1234-5678'),
              ('tel 82 10 1234 5678', '82 10 1234 5678'), ('0507-1234-5678', '0507-1234-5678'),
              ('1644-1234', '1644-1234'), ('010-1234-5678.', '010-1234-5678.'), ('zero-width', '010\u200b1234\u200b5678'),
              ('middle-dot', '010·1234·5678'), ('kr words', '공일공 일이삼사 오육칠팔')]:
    cases.append(('phone', nm, 'call ' + p + ' now', ''.join(ch for ch in p if ch.isdigit())[-4:] if any(ch.isdigit() for ch in p) else p, False))
c('rrn', 'rrn dot', '900101.1234567', '1234567')
c('rrn', 'rrn slash', '900101/1234567', '1234567')
c('rrn', 'rrn zero-width', '900101\u200b1234567', '1234567', False)
c('mail', 'user [at] x [dot] com', 'user [at] example [dot] com', 'user', False)
c('mail', 'fullwidth @', 'user＠example.com', 'user')
c('mail', 'a@b.c short tld', 'mail me@x.io', 'me@x.io')
c('mail', 'a at b.com spaced', 'user @ example.com', 'user @ example.com')
c('mail', 'plus quoted', 'x "john doe"@corp.example y', 'john doe')
c('mail', 'newline in mail', 'ann@\nexample.com', 'ann')

@pytest.mark.parametrize("group,name,text,secret,card", cases, ids=[f"{row[0]}-{row[1]}" for row in cases])
def test_bypass_shapes_remove_the_entire_synthetic_value(group, name, text, secret, card):
    safe, counts = redact(text)
    assert secret not in safe
    assert safe != text and counts
    assert redact(safe) == (safe, {})


@pytest.mark.parametrize("size", [32, 33, 39])
@pytest.mark.parametrize("template", ["{}", "value {} end", "commit {}", "HEAD {}"])
def test_shorter_than_commit_hex_is_never_exempt(size, template):
    value = ("abcdef0123456789" * 4)[:size]
    safe, counts = redact(template.format(value))
    assert value not in safe and counts


@pytest.mark.parametrize("parts,separator", [([32, 32], "\n"), ([31, 33], " "), ([2] * 32, ":")])
def test_split_hex_is_one_redacted_span(parts, separator):
    value = "abcdef0123456789" * 4
    fragments, index = [], 0
    for size in parts:
        fragments.append(value[index:index + size])
        index += size
    text = separator.join(fragments)
    assert redact(text) == ("[REDACTED:opaque]", {"opaque": 1})


@pytest.mark.parametrize("path", [
    ".codex/auth.json", ".claude.json", ".claude/.credentials", ".claude/.credentials.json",
    ".gemini/oauth_creds.json", ".config/gh/hosts.yml", ".docker/config.json",
    ".kube/config", ".pypirc", "application_default_credentials.json", "app.env.local",
    "app.env.production", "access_token_prod.json", "kubeconfig-prod", "kubeconfig.yaml",
    "firebase-adminsdk-synthetic.json", ".htpasswd", "payroll.csv", "salary.xlsx",
    "급여명세.xlsx", "연봉계약.xlsx", "인사명단.csv",
])
def test_additional_sensitive_credentials_and_personnel_paths(path):
    assert sensitive_path(path)


def test_secret_container_covers_all_values_but_preserves_the_next_section():
    text = '[tokens]\nprod = "SyntheticAlpha"\ndev = "SyntheticBeta"\n[public]\nport = 7'
    safe, counts = redact(text)
    assert "SyntheticAlpha" not in safe and "SyntheticBeta" not in safe
    assert "[public]\nport = 7" in safe and counts


def test_yaml_container_covers_every_indented_value_only():
    text = 'tokens:\n  prod: SyntheticAlpha\n  dev: SyntheticBeta\npublic: hello'
    safe, counts = redact(text)
    assert "SyntheticAlpha" not in safe and "SyntheticBeta" not in safe
    assert "public: hello" in safe and counts

@pytest.mark.parametrize("header", ["[tokens]", "tokens:"])
def test_packet_block_redaction_keeps_context_before_the_excerpt_window(header):
    from tests.test_claims_packets import candidate, packetize, source
    rows = [header] + [f'  prod{i}: "PrivateValue{i:02d}"' for i in range(25)]
    item = candidate(ranges=[{"path": "alpha.py", "start": 20, "end": 20}])
    packets, _ = packetize([item], [source("\n".join(rows))])
    assert "PrivateValue" not in str(packets)


def test_numbered_packet_secret_block_keeps_unmasked_source_line_numbers():
    from tests.test_claims_packets import candidate, packetize, source
    rows = '[tokens]\nprod = "PrivateAlpha"\ndev = "PrivateBeta"\n[public]\nport = 7'
    packets, summary = packetize([candidate()], [source(rows)])
    assert summary["sent_count"] == 1
    text = packets[0]["data_block"][0]["excerpts"][0]["text"]
    assert "PrivateAlpha" not in text and "PrivateBeta" not in text
    assert "4: [public]\n5: port = 7" in text
