import pytest

from smtw.claims.packets import redact
from tests.test_claims_p1e_inherited import references


@pytest.mark.parametrize("tail", ["`:99 + 1`", "`:99)`"])
def test_embedded_slice_is_not_an_inherited_line_range(tail):
    refs = references("`alpha.py:1`, " + tail)
    assert [(c.start, c.inherited_binding) for c in refs] == [(1, False)]


@pytest.mark.parametrize("tail", ['":99"', "'(:99)'", '“:99”'])
def test_quoted_colon_is_data_even_when_gap_has_only_punctuation(tail):
    refs = references("`alpha.py:1`, " + tail)
    assert [(c.start, c.inherited_binding) for c in refs] == [(1, False)]


@pytest.mark.parametrize("length", range(32, 40))
def test_opaque_hex_in_prose_cannot_use_short_commit_exception(length):
    value = ("a1b2c3d4" * 5)[:length]
    redacted, counts = redact("opaque value (" + value + ") end")
    assert value not in redacted
    assert counts["opaque"] == 1


@pytest.mark.parametrize("key", [".password", "-password"])
def test_credential_field_boundary_includes_leading_key_punctuation(key):
    text, counts = redact(key + " = 'synthetic-only-value'")
    assert text == "[REDACTED:password]"
    assert counts == {"password": 1}

@pytest.mark.parametrize("report", ['"alpha.py:1, :99"', '“alpha.py:1 및 :99”'])
def test_quoted_string_containing_the_preceding_reference_cannot_inherit(report):
    refs = references(report)
    assert [(c.start, c.inherited_binding) for c in refs] == [(1, False)]
