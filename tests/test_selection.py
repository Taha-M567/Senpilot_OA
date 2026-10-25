import pytest

from senpilot_agent.catalog import _candidate, finalize_catalog
from senpilot_agent.collection import CATEGORIES
from senpilot_agent.selection import EmailRequest, RequestFailure, extract_matter_number, read_json, validate_selection


def catalog_for(entries):
    """Real boundary models built from small illustrative listing observations."""
    counts = dict.fromkeys(CATEGORIES, 0)
    documents = []
    for category, number, title in entries:
        position = counts[category]
        counts[category] += 1
        documents.append(_candidate(category, {
            'identifiers': [number], 'titles': [title], 'security': ['Public'],
            'dates': ['01/02/2026'], 'extensions': ['.pdf'],
            'listing_position': position, 'raw_text': f'{number}\n{title}\nPublic',
        }, len(documents)+1))
    return finalize_catalog('M12205', counts, documents)


@pytest.fixture
def catalog():
    return catalog_for([('Other Documents','100001','Board Order'),
                        ('Exhibits','H-1','Application'),
                        ('Recordings','01/07/2026','Day 1')])


def selection_for(catalog, indices=(0,)):
    entries = [catalog['documents'][i] for i in indices]
    return {'status':'ready', 'matter_number':catalog['matter_number'],
            'categories':list(dict.fromkeys(d['category'] for d in entries)),
            'category_basis':'explicit', 'assumptions':[], 'limit':10,
            'ranked_documents':[{'candidate_id':d['candidate_id'],'reason':'Matches requested listing',
                                 'matched_text':d['title'] or d['witnesses'][0]} for d in entries], 'message':None}


@pytest.mark.parametrize('subject,body', [('m12205','M12205'),('Matter: M00000.',''),
    ('','Please send (M12205).'),('M12205','M12205 M12205')])
def test_regex_extracts_one_distinct_complete_ascii_matter(subject, body):
    assert extract_matter_number(subject,body) in {'M12205','M00000'}


@pytest.mark.parametrize('body', ['', 'M1220', 'M122055', 'XM12205', 'M12205X',
    'M１２２０５', 'M١٢٢٠٥', 'M12205_other', 'éM12205', 'M12205 M12345'])
def test_regex_rejects_partial_non_ascii_or_multiple_matters(body):
    with pytest.raises(RequestFailure) as exc:
        extract_matter_number('',body)
    assert exc.value.status == 'invalid_request'


def test_email_contract_and_duplicate_json_keys(tmp_path):
    assert EmailRequest.from_dict({'subject':'','body_text':'M12205'}).body_text == 'M12205'
    for value in ({}, {'subject':'a','body_text':None}, {'subject':'a','body_text':'b','thread_id':'x'}):
        with pytest.raises((TypeError,ValueError)):
            EmailRequest.from_dict(value)
    path=tmp_path/'request.json'
    path.write_text('{"subject":"a","subject":"b","body_text":"M12205"}')
    with pytest.raises(ValueError,match='Duplicate'):
        read_json(path)


def test_mapping_preserves_model_order_and_native_references(catalog):
    result=validate_selection(selection_for(catalog,(1,0)),catalog)
    assert [d.reference for d in result.selected()] == ['H-1','100001']
    assert [d.priority for d in result.selected()] == [1,2]
    assert 'reason' not in result.documents[0].to_dict()


@pytest.mark.parametrize('change', [
    {'matter_number':'M99999'}, {'limit':True}, {'limit':0}, {'limit':11},
    {'categories':['Unknown']}, {'categories':['Exhibits']}, {'extra':'bad'},
    {'status':'clarify'}, {'message':'question'}, {'ranked_documents':[]},
    {'category_basis':'inferred'}, {'assumptions':[None]},
])
def test_invalid_selection_cannot_become_download_request(catalog,change):
    value=selection_for(catalog)
    value.update(change)
    with pytest.raises((TypeError,ValueError)):
        validate_selection(value,catalog)


@pytest.mark.parametrize('change', [ {'candidate_id':'invented'}, {'matched_text':'not in this title'},
                                     {'reason':''}, {'extra':'URL'} ])
def test_invalid_candidate_or_evidence(catalog,change):
    value=selection_for(catalog)
    value['ranked_documents'][0].update(change)
    with pytest.raises(ValueError):
        validate_selection(value,catalog)


def test_private_duplicate_or_overlimit_selections_rejected(catalog):
    value=selection_for(catalog)
    value['ranked_documents']*=2
    with pytest.raises(ValueError,match='duplicate'):
        validate_selection(value,catalog)
    value=selection_for(catalog,(0,1)); value['limit']=1
    with pytest.raises(ValueError,match='limit'):
        validate_selection(value,catalog)
    catalog['documents'][0]['eligible']=False
    with pytest.raises(ValueError,match='ineligible'):
        validate_selection(selection_for(catalog),catalog)


def test_cross_category_alias_is_not_two_downloads():
    catalog=catalog_for([('Key Documents','100001','Board Order'),('Other Documents','100001','Board Order')])
    with pytest.raises(ValueError,match='Duplicate document'):
        validate_selection(selection_for(catalog,(0,1)),catalog)


def test_conflicting_native_references_and_counts_fail_catalog():
    with pytest.raises(ValueError,match='within a category'):
        catalog_for([('Exhibits','H-1','Application'),('Exhibits','H-1','Different document')])
    with pytest.raises(ValueError,match='cross-category'):
        catalog_for([('Key Documents','100001','Board Order'),('Other Documents','100001','Different document')])
    catalog=catalog_for([('Exhibits','H-1','Application')])
    catalog['category_counts']['Exhibits']=2
    with pytest.raises(ValueError,match='counts disagree'):
        finalize_catalog('M12205',catalog['category_counts'],catalog['documents'])


def test_global_ten_document_cap_is_not_per_category():
    catalog=catalog_for([('Exhibits',f'H-{i}',f'Evidence {i}') for i in range(6)] +
                        [('Other Documents',str(100001+i),f'Filing {i}') for i in range(5)])
    value=selection_for(catalog,tuple(range(11)))
    with pytest.raises(ValueError,match='limit'):
        validate_selection(value,catalog)


@pytest.mark.parametrize('status',['insufficient_detail','no_matches'])
def test_non_ready_is_terminal_with_no_download_request(catalog,status):
    value=selection_for(catalog)
    value.update(status=status,ranked_documents=[],message='Submit a new detailed request from scratch.')
    assert validate_selection(value,catalog) is None
    value['ranked_documents']=selection_for(catalog)['ranked_documents']
    with pytest.raises(ValueError):
        validate_selection(value,catalog)


def test_default_overview_requires_assumption_and_excludes_audio(catalog):
    value=selection_for(catalog,(2,))
    value.update(category_basis='default_overview',assumptions=['Generic request; choose an overview.'])
    with pytest.raises(ValueError,match='recordings'):
        validate_selection(value,catalog)
    value=selection_for(catalog,(0,1))
    value.update(category_basis='default_overview',assumptions=['Generic overview.'])
    assert len(validate_selection(value,catalog).documents)==2


def test_repeated_dates_need_distinguishing_title_or_witness():
    catalog=catalog_for([('Recordings','01/07/2026','Part 1'),('Recordings','01/07/2026','Part 2'),
                        ('Transcripts','01/07/2026','Smith'),('Transcripts','01/07/2026','Jones')])
    assert all(d['eligible'] for d in catalog['documents'])
    request=validate_selection(selection_for(catalog,(0,1,2,3)),catalog)
    assert [d.title for d in request.documents]==['Part 1','Part 2','Smith','Jones']
    ambiguous=catalog_for([('Transcripts','01/07/2026','Smith'),('Transcripts','01/07/2026','Smith')])
    assert not any(d['eligible'] for d in ambiguous['documents'])
