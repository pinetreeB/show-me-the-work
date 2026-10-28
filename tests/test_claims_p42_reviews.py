"""Keep every earlier bypass family active with the real optional parser."""
import pytest

from smtw.claims.check import run
from tests.test_claims_p41_fix1 import NON_DEFINITIONS
from tests.test_claims_p41_fix2 import BARE_ASSIGNMENTS
from tests.test_claims_p41_fix3 import FIX2_REVIEW_CASES
from tests.test_claims_p41_fix4 import EXPRESSION_DECLARATIONS
from tests.test_claims_p41_fix5 import GROK_FIX4_CASES
from tests.test_claims_p42_d1 import case, parser, projected


LANGUAGES = ['.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx']
CASES = [(suffix, text) for text in BARE_ASSIGNMENTS for suffix in LANGUAGES]
CASES += [(suffix, text) for _, suffix, text in FIX2_REVIEW_CASES]
# The ASI example is a valid module binding, pre-registered as a D1 positive.
CASES += [(suffix, text) for i, text in enumerate(EXPRESSION_DECLARATIONS) if i != 8
          for suffix in LANGUAGES]
CASES += [(suffix, text) for text in GROK_FIX4_CASES for suffix in ['.js', '.ts']]
CASES += [('.py', text) for text in NON_DEFINITIONS]
CASES += [('.js', text) for text in [
    'const table = {"moved_helper": 1};', 'const table = {moved_helper: 1};',
    'invoke({moved_helper: 1});', 'if (moved_helper) { consume(); }',
    'const message = "def moved_helper(): pass";', 'const matcher = /; moved_helper = /;',
    'function wrapper(\n moved_helper = 1\n) {}',
]]


@pytest.mark.parametrize('suffix,other', CASES)
def test_claims_p42_previous_bypasses_keep_both_routes(parser, tmp_path, suffix, other):
    report, kwargs = case(tmp_path, other, suffix)
    baseline = run(report, **kwargs)
    assert projected(run(report, **kwargs, js_parser=parser)) == projected(baseline)
