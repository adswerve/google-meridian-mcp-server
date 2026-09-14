"""The single payload extractor every MCP driver in this repo must use."""

import types

from scripts.validation.payloads import content_to_obj, extract, unwrap


def _result(**kwargs):
    """A stand-in for fastmcp's CallToolResult with only the attrs we read."""
    base = {"structured_content": None, "data": None, "content": []}
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def test_structured_content_wins_over_data():
    result = _result(structured_content={"a": 1}, data={"a": 2})
    assert content_to_obj(result) == {"a": 1}


def test_falls_back_to_data_when_structured_content_missing():
    result = _result(data={"a": 2})
    assert content_to_obj(result) == {"a": 2}


def test_falls_back_to_decoded_text_block():
    block = types.SimpleNamespace(text='{"a": 3}')
    assert content_to_obj(_result(content=[block])) == {"a": 3}


def test_non_json_text_block_is_returned_verbatim():
    block = types.SimpleNamespace(text="not json")
    assert content_to_obj(_result(content=[block])) == "not json"


def test_unwrap_collapses_single_result_key():
    assert unwrap({"result": [1, 2]}) == [1, 2]


def test_unwrap_leaves_multi_key_payloads_alone():
    payload = {"result": 1, "other": 2}
    assert unwrap(payload) == payload


def test_extract_composes_both_steps():
    assert extract(_result(structured_content={"result": ["m1"]})) == ["m1"]
