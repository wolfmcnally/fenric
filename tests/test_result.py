"""Result validation: a window's payload is checked against its spec."""

from __future__ import annotations

from typing import Any

import pytest

from fenric.result import ResultError, validate_result
from fenric.spec import validate_spec

ASK = validate_spec(
    {
        "verb": "ask",
        "questions": [
            {"id": "why", "prompt": "Why?"},
            {"id": "which", "prompt": "Which?", "options": ["red", "green"], "other": True},
            {"id": "all", "prompt": "All?", "kind": "multi", "options": ["x", "y", "z"]},
        ],
    }
)
FORM = validate_spec(
    {
        "verb": "form",
        "components": [
            {"type": "field", "id": "name", "label": "Name", "required": True},
            {
                "type": "card",
                "children": [
                    {"type": "select", "id": "size", "label": "Size", "options": ["s", "m"]},
                    {"type": "checkbox", "id": "agree", "label": "Agree"},
                ],
            },
        ],
    }
)


def ok(**values: Any) -> dict[str, Any]:
    return {"action": "ok", "values": {key.replace("__", "."): v for key, v in values.items()}}


def refusal(spec: Any, payload: object) -> ResultError:
    with pytest.raises(ResultError) as raised:
        validate_result(spec, payload)
    return raised.value


def test_confirm_maps_yes_and_no_to_booleans() -> None:
    spec = validate_spec({"verb": "confirm", "message": "m"})
    assert validate_result(spec, {"action": "yes", "values": {}}) is True
    assert validate_result(spec, {"action": "no", "values": {}}) is False
    assert refusal(spec, {"action": "ok", "values": {}}).path == "result.action"


def test_pick_maps_indexes_to_option_ids() -> None:
    options = [{"id": "first", "label": "One"}, {"id": "second", "label": "Two"}]
    single = validate_spec({"verb": "pick", "message": "m", "options": options})
    assert validate_result(single, ok(choice=1)) == "second"
    multiple = validate_spec({"verb": "pick", "message": "m", "options": options, "multiple": True})
    assert validate_result(multiple, ok(choice=[1, 0])) == ["first", "second"]
    assert validate_result(multiple, ok(choice=[])) == []
    assert refusal(multiple, ok(choice=[0, 0])).problem == "repeats an option"


def test_ask_returns_an_object_keyed_by_question_id() -> None:
    answers = validate_result(ASK, ok(q0="because", q1=1, q1__other="", q2=[2, 0]))
    assert answers == {"why": "because", "which": "green", "all": ["x", "z"]}
    typed = validate_result(ASK, ok(q0="because", q1="other", q1__other=" teal ", q2=[]))
    assert typed == {"why": "because", "which": "teal", "all": []}


def test_an_index_outside_the_options_is_refused() -> None:
    for bad in (2, -1, True, "1", 1.0):
        error = refusal(ASK, ok(q0="a", q1=bad, q1__other="", q2=[]))
        assert (error.path, error.problem) == ("questions[1]", "is not one of the offered options")
    assert refusal(ASK, ok(q0="a", q1=0, q1__other="", q2=[3])).path == "questions[2]"


def test_a_missing_required_answer_is_refused() -> None:
    blank = refusal(ASK, ok(q0="   ", q1=0, q1__other="", q2=[]))
    assert (blank.path, blank.problem) == ("questions[0]", "has no answer")
    unchosen = refusal(ASK, ok(q0="a", q1=None, q1__other="", q2=[]))
    assert unchosen.path == "questions[1]"
    empty_other = refusal(ASK, ok(q0="a", q1="other", q1__other="  ", q2=[]))
    assert empty_other.path == "questions[1]"


def test_keys_must_match_the_dialogs_inputs_exactly() -> None:
    missing = refusal(ASK, ok(q0="a", q1=0, q2=[]))
    assert missing.path == "result.values"
    extra = refusal(ASK, ok(q0="a", q1=0, q1__other="", q2=[], q3="SENTINEL"))
    assert extra.path == "result.values" and "SENTINEL" not in str(extra)
    assert refusal(ASK, "SENTINEL").path == "result"
    assert refusal(ASK, {"action": "ok", "values": []}).path == "result.values"


def test_form_returns_an_object_keyed_by_input_id() -> None:
    assert validate_result(FORM, ok(c0="Ada", c1=1, c2=True)) == {
        "name": "Ada",
        "size": "m",
        "agree": True,
    }
    assert validate_result(FORM, ok(c0="Ada", c1=None, c2=False))["size"] is None
    assert refusal(FORM, ok(c0="", c1=0, c2=False)).path == "inputs[0]"
    assert refusal(FORM, ok(c0="Ada", c1=0, c2="yes")).path == "inputs[2]"


def test_status_verbs_accept_only_a_bare_ok() -> None:
    notify = validate_spec({"verb": "notify", "message": "m"})
    assert validate_result(notify, ok()) is None
    assert refusal(notify, ok(extra="SENTINEL")).path == "result.values"
    assert refusal(notify, {"action": "yes", "values": {}}).path == "result.action"


def test_wizard_result_requires_exact_active_branch_and_ignores_other_text_for_conditions() -> None:
    spec = validate_spec(
        {
            "verb": "ask",
            "presentation": "wizard",
            "questions": [
                {
                    "id": "route",
                    "prompt": "Route",
                    "options": ["a", "b"],
                    "other": True,
                    "required": False,
                },
                {
                    "id": "areas",
                    "prompt": "Areas",
                    "kind": "multi",
                    "options": ["web", "cli"],
                    "other": True,
                    "when": {"question": "route", "equals": "a"},
                },
                {"id": "note", "prompt": "Note", "when": {"question": "areas", "includes": "web"}},
                {"id": "optional", "prompt": "Optional", "required": False},
            ],
        }
    )
    assert validate_result(
        spec, ok(q0=0, q0__other="", q1=[0], q1__other="", q2="draft", q3="")
    ) == {"route": "a", "areas": ["web"], "note": "draft", "optional": ""}
    assert validate_result(spec, ok(q0=0, q0__other="", q1=[], q1__other="web", q3="")) == {
        "route": "a",
        "areas": ["web"],
        "optional": "",
    }
    for route, other in ((1, ""), (None, ""), ("other", "a")):
        payload = ok(q0=route, q0__other=other, q3="")
        assert set(validate_result(spec, payload)) == {"route", "optional"}
        assert (
            refusal(spec, {**payload, "values": {**payload["values"], "q2": "stale"}}).path
            == "result.values"
        )
    assert (
        refusal(spec, ok(q0=0, q0__other="", q1=[0], q1__other="", q2="", q3="")).path
        == "questions[2]"
    )
    assert (
        refusal(spec, ok(q0=0, q0__other="", q1=[0], q1__other="", q3="")).path == "result.values"
    )
