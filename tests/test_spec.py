"""Spec validation: what is accepted, and that every refusal is value-free."""

from __future__ import annotations

import pytest

from fenric.spec import VERBS, SpecError, validate_spec

MINIMAL = {
    "notify": {"message": "m"},
    "confirm": {"message": "m"},
    "pick": {"message": "m", "options": ["a", "b"]},
    "ask": {"questions": [{"id": "q", "prompt": "p"}]},
    "secret": {"fields": ["token"], "store": "keep {name}"},
    "form": {"components": [{"type": "field", "id": "f", "label": "l"}]},
    "custom": {"html": "<p>x</p>"},
    "chart": {"code": "d3.select(root);"},
    "html": {"root": ".", "entry": "index.html", "files": ["index.html"]},
}


def refusal(spec: object) -> SpecError:
    with pytest.raises(SpecError) as raised:
        validate_spec(spec)
    return raised.value


def test_every_verb_accepts_its_minimal_spec() -> None:
    assert set(MINIMAL) == set(VERBS) and len(VERBS) == 9
    for verb, fields in MINIMAL.items():
        spec = validate_spec({"verb": verb, **fields})
        assert spec.verb == verb
        assert spec.app == "Fenric"
        assert spec.timeout is None
    payload = '\ufeff  literal "quotes"\r\n\tlast\n'
    assert (
        validate_spec({"verb": "notify", "message": "m", "copy_text": payload}).fields["copy_text"]
        == payload
    )
    assert refusal({"verb": "notify", "message": "m", "copy_text": "\ud800"}).path == "copy_text"
    assert refusal({"verb": "notify", "message": "m", "copy_text": "  "}).path == "copy_text"


def test_unknown_verb_is_refused_without_quoting_it() -> None:
    error = refusal({"verb": "SENTINEL-verb", "message": "m"})
    assert error.path == "verb"
    assert "SENTINEL" not in str(error)


def test_unknown_field_is_refused_without_naming_it() -> None:
    error = refusal({"verb": "notify", "message": "m", "SENTINEL_key": 1})
    assert (error.path, error.problem) == ("spec", "has an unknown field")
    nested = refusal(
        {"verb": "pick", "message": "m", "options": [{"label": "a", "SENTINEL_key": 1}]}
    )
    assert (nested.path, nested.problem) == ("options[0]", "has an unknown field")


def test_wrong_type_names_the_field_path() -> None:
    error = refusal(
        {"verb": "ask", "questions": [{"id": "a", "prompt": "p"}, {"id": "b", "prompt": 7}]}
    )
    assert error.path == "questions[1].prompt"
    assert refusal({"verb": "notify"}).path == "message"
    assert refusal(["not", "an", "object"]).path == "spec"


def test_duplicate_ids_are_refused() -> None:
    options = refusal({"verb": "pick", "message": "m", "options": ["same", "same"]})
    assert options.path == "options[1].id"
    questions = refusal(
        {"verb": "ask", "questions": [{"id": "a", "prompt": "p"}, {"id": "a", "prompt": "p"}]}
    )
    assert questions.path == "questions[1].id"
    inputs = refusal(
        {
            "verb": "form",
            "components": [
                {"type": "checkbox", "id": "x", "label": "l"},
                {"type": "card", "children": [{"type": "field", "id": "x", "label": "l"}]},
            ],
        }
    )
    assert inputs.path == "components[1].children[0].id"


def test_form_component_outside_the_catalog_names_its_path() -> None:
    error = refusal(
        {
            "verb": "form",
            "components": [
                {"type": "heading", "text": "t"},
                {"type": "SENTINEL-widget", "id": "x", "label": "SENTINEL-label"},
            ],
        }
    )
    assert error.path == "components[1].type"
    assert error.problem == "is not in the form catalog"
    assert "SENTINEL" not in str(error)


def test_unsupported_form_catalog_version_is_refused() -> None:
    components = [{"type": "text", "text": "t"}]
    assert validate_spec({"verb": "form", "catalog": 1, "components": components})
    assert refusal({"verb": "form", "catalog": 2, "components": components}).path == "catalog"
    assert refusal({"verb": "form", "catalog": 1.0, "components": components}).path == "catalog"
    heading = [{"type": "heading", "text": "t", "level": 1.0}]
    assert refusal({"verb": "form", "components": heading}).path == "components[0].level"


def test_secret_without_a_store_command_is_refused() -> None:
    error = refusal({"verb": "secret", "fields": ["token"]})
    assert (error.path, error.problem) == (
        "store",
        "or into is required: the secret verb needs a store command or a file",
    )
    assert refusal({"verb": "secret", "fields": ["token"], "store": "  "}).path == "store"
    assert refusal({"verb": "secret", "fields": ["token"], "store": "keep\x00 it"}).path == "store"
    assert refusal({"verb": "secret", "fields": ["bad name"], "store": "s"}).path == (
        "fields[0].name"
    )


def test_secret_takes_exactly_one_destination() -> None:
    fields = {"verb": "secret", "fields": ["token"]}
    by_file = validate_spec({**fields, "into": "some/.env"})
    assert (by_file.store, by_file.into) == (None, "some/.env")
    by_command = validate_spec({**fields, "store": "keep {name}"})
    assert (by_command.store, by_command.into) == ("keep {name}", None)
    both = refusal({**fields, "store": "keep {name}", "into": "some/.env"})
    assert (both.path, both.problem) == ("into", "cannot be given together with store")
    for bad in ("", 7, ["some/.env"], "some\x00/.env"):
        assert refusal({**fields, "into": bad}).path == "into"
    assert refusal({"verb": "notify", "message": "m", "into": "some/.env"}).path == "spec"


def test_timeout_must_be_positive_and_bounded() -> None:
    assert validate_spec({"verb": "notify", "message": "m", "timeout": 5}).timeout == 5.0
    for bad in (0, -1, 3601, True, "5", float("inf"), float("nan"), 10**400):
        assert refusal({"verb": "notify", "message": "m", "timeout": bad}).path == "timeout"


def test_options_and_questions_are_normalized() -> None:
    spec = validate_spec(
        {
            "verb": "ask",
            "questions": [
                {"id": "t", "prompt": "p"},
                {"id": "c", "prompt": "p", "options": ["a", {"label": "B", "id": "b"}]},
                {"id": "m", "prompt": "p", "kind": "multi", "options": ["a"], "other": True},
            ],
        }
    )
    text, choice, multi = spec.fields["questions"]
    assert (text["kind"], text["required"]) == ("text", True)
    assert choice["kind"] == "choice"
    assert choice["options"] == [
        {"id": "a", "label": "a", "detail": None},
        {"id": "b", "label": "B", "detail": None},
    ]
    assert (multi["required"], multi["other"]) == (False, True)
    assert (
        refusal(
            {
                "verb": "ask",
                "questions": [{"id": "t", "prompt": "p", "kind": "text", "other": True}],
            }
        ).path
        == "questions[0]"
    )


def test_wizard_conditions_resolve_only_prior_offered_choices() -> None:
    base = {
        "verb": "ask",
        "presentation": "wizard",
        "questions": [
            {"id": "route", "prompt": "Route", "options": ["a", "b"]},
            {"id": "details", "prompt": "Details", "when": {"question": "route", "equals": "a"}},
        ],
    }
    assert validate_spec(base).fields["questions"][1]["when"] == {
        "question": 0,
        "option": 0,
        "operator": "equals",
    }
    for condition in (
        None,
        [],
        {},
        {"question": "route"},
        {"question": "route", "equals": "a", "includes": "a"},
        {"question": "details", "equals": "a"},
        {"question": "SENTINEL", "equals": "a"},
        {"question": "route", "equals": "SENTINEL"},
        {"question": "route", "includes": "a"},
        {"question": 7, "equals": "a"},
    ):
        raw = {
            **base,
            "questions": [base["questions"][0], {**base["questions"][1], "when": condition}],
        }
        error = refusal(raw)
        assert error.path.startswith("questions[1].when") and "SENTINEL" not in str(error)
    assert refusal({**base, "presentation": "form"}).path == "questions[1].when"
    for presentation in (None, True, [], {}, "SENTINEL"):
        assert refusal({**base, "presentation": presentation}).path == "presentation"
    text_source = {**base, "questions": [{"id": "route", "prompt": "Route"}, base["questions"][1]]}
    assert refusal(text_source).path == "questions[1].when"

    multi_source = {
        **base,
        "questions": [{**base["questions"][0], "kind": "multi"}, base["questions"][1]],
    }
    assert refusal(multi_source).path == "questions[1].when"
