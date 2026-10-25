import importlib.util
import json
from pathlib import Path

from test_selection import selection_for


spec=importlib.util.spec_from_file_location('evaluate_selection',Path(__file__).resolve().parents[1]/'scripts/evaluate_selection.py')
evaluation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_labelled_cases_and_judge_accept_tiers_but_reject_wrong_or_excluded_matches():
    fixtures=json.loads(evaluation.FIXTURES.read_text(encoding='utf-8'))
    assert len(fixtures['cases'])>=12
    case=next(c for c in fixtures['cases'] if c['name']=='orders_then_compliance')
    catalog=evaluation.case_catalog(fixtures,case)
    value=selection_for(catalog,(0,4,5))
    assert evaluation.judge(case,value,catalog)['passed']
    value=selection_for(catalog,(0,5,4))
    assert evaluation.judge(case,value,catalog)['passed']
    value=selection_for(catalog,(4,0,5))
    assert not evaluation.judge(case,value,catalog)['passed']
    value=selection_for(catalog,(0,6))
    assert not evaluation.judge(case,value,catalog)['passed']
    for case in fixtures['cases']:
        catalog=evaluation.case_catalog(fixtures,case)
        assert catalog['complete']
        assert catalog['eligible_count']==len(catalog['documents'])
