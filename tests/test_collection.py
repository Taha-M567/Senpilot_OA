import json

import pytest

from senpilot_agent.collection import CollectionRequest, DocumentPriority, load_collection_request


def request_data():
    return {"matter_number": "M12205", "documents": [
        {"category": "Other Documents", "document_number": "102674", "priority": 2},
        {"category": "Exhibits", "document_number": "H-1", "priority": 1},
        {"category": "Key Documents", "document_number": "97289", "priority": 2},
    ]}


def test_priorities_have_stable_ties_and_one_global_cap():
    request = CollectionRequest.from_dict({**request_data(), "limit": 2})
    assert [d.document_number for d in request.selected()] == ["H-1", "102674"]
    assert request.to_dict() == {**request_data(), "limit": 2}
    many = CollectionRequest("M12205", tuple(
        DocumentPriority("Other Documents", str(i), 20 - i) for i in range(15)
    ))
    assert [d.document_number for d in many.selected()] == [str(i) for i in range(14, 4, -1)]


@pytest.mark.parametrize("change", [
    {"matter_number": "m12205"}, {"matter_number": "M１２２０５"}, {"matter_number": None},
    {"limit": True}, {"limit": 0}, {"limit": 11}, {"limit": 1.0}, {"limit": "10"},
    {"documents": None}, {"documents": {}}, {"unknown": 1},
])
def test_invalid_request_fields_are_rejected(change):
    with pytest.raises(ValueError):
        CollectionRequest.from_dict({**request_data(), **change})


@pytest.mark.parametrize("entry", [
    {"category": "Other", "document_number": "1", "priority": 1},
    {"category": "Exhibits", "document_number": "../../file", "priority": 1},
    {"category": "Exhibits", "document_number": "H-1\n", "priority": 1},
    {"category": "Exhibits", "document_number": " H-1", "priority": 1},
    {"category": "Other Documents", "document_number": 123, "priority": 1},
    {"category": "Other Documents", "document_number": "", "priority": 1},
    {"category": "Other Documents", "document_number": "1", "priority": False},
    {"category": "Other Documents", "document_number": "1", "priority": 0},
    {"category": "Other Documents", "document_number": "1", "priority": 1.5},
    {"category": "Other Documents", "document_number": "1", "priority": 1, "url": "https://example.com/"},
    {"category": "Other Documents", "priority": 1}, None,
])
def test_invalid_document_entries_are_rejected(entry):
    with pytest.raises(ValueError):
        CollectionRequest.from_dict({"matter_number": "M12205", "documents": [entry]})


def test_duplicate_document_across_categories_is_rejected():
    data = request_data()
    data["documents"][2]["document_number"] = "102674"
    with pytest.raises(ValueError, match="Duplicate"):
        CollectionRequest.from_dict(data)


def test_json_loading_and_duplicate_keys(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request_data()), encoding="utf-8-sig")
    assert load_collection_request(path).selected()[0].document_number == "H-1"
    path.write_text('{"matter_number":"M12205","matter_number":"M00000","documents":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        load_collection_request(path)


def test_empty_request_is_valid_and_not_a_request_for_arbitrary_files():
    assert CollectionRequest.from_dict({"matter_number": "M12205", "documents": []}).selected() == ()


def test_hearing_dates_are_explicit_and_round_trip_with_title():
    entry = {'category':'Recordings','hearing_date':'01/07/2026','title':'Day 1','priority':1}
    data = {'matter_number':'M12205','limit':10,'documents':[entry]}
    request = CollectionRequest.from_dict(data)
    assert request.selected()[0].reference == '01/07/2026'
    assert request.to_dict() == data


@pytest.mark.parametrize('entry', [
    {'category':'Transcripts','document_number':'123','priority':1},
    {'category':'Transcripts','hearing_date':'02/30/2026','priority':1},
    {'category':'Recordings','hearing_date':'2026-01-07','priority':1},
    {'category':'Recordings','hearing_date':'01/07/2026','priority':1,'title':''},
    {'category':'Other Documents','hearing_date':'01/07/2026','priority':1},
])
def test_invalid_hearing_references(entry):
    with pytest.raises(ValueError):
        CollectionRequest.from_dict({'matter_number':'M12205','documents':[entry]})
