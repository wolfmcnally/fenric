"""Validate what a window returned against the spec that produced it.

The window is not trusted to return well-formed data. Choices come back as
option indexes, never as labels or ids, so every answer can be checked
exactly. A :class:`ResultError` is value-free in the same way a
:class:`~fenric.spec.SpecError` is.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fenric.images import MAX_COMMENT_LENGTH
from fenric.spec import Spec, form_inputs

OTHER = "other"


class ResultError(ValueError):
    """A window returned something the spec did not ask for."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"{path}: {problem}")
        self.path = path
        self.problem = problem


def validate_result(spec: Spec, payload: object) -> Any:
    """Return the caller-facing value for ``payload``, or raise :class:`ResultError`.

    Status-only verbs return ``None``. The ``secret`` verb returns its values
    as ``(field index, value)`` pairs for the store command; they are never
    part of what the caller sees.
    """
    if not isinstance(payload, Mapping) or set(payload) != {"action", "values"}:
        raise ResultError("result", "is not a result object")
    action, values = payload["action"], payload["values"]
    if not isinstance(values, Mapping):
        raise ResultError("result.values", "is not an object")
    verb = spec.verb
    if verb == "confirm":
        _keys(values, set())
        if action not in ("yes", "no"):
            raise ResultError("result.action", "is not an answer to this dialog")
        return action == "yes"
    if action != "ok":
        raise ResultError("result.action", "is not an answer to this dialog")
    if verb in ("notify", "custom", "chart", "html"):
        _keys(values, set())
        return None
    if verb == "pick":
        _keys(values, {"choice"})
        options = spec.fields["options"]
        if spec.fields["multiple"]:
            return [options[i]["id"] for i in _indexes(values["choice"], options, "result.choice")]
        return options[_index(values["choice"], options, "result.choice")]["id"]
    if verb == "ask":
        return _ask(spec, values)
    if verb == "form":
        return _form(spec, values)
    return _secret(spec, values)


def _keys(values: Mapping[str, Any], expected: set[str]) -> None:
    if set(values) != expected:
        raise ResultError("result.values", "does not match the dialog's inputs")


def _index(value: object, options: list[Any], path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(options):
        raise ResultError(path, "is not one of the offered options")
    return value


def _indexes(value: object, options: list[Any], path: str) -> list[int]:
    if not isinstance(value, list):
        raise ResultError(path, "is not a list of options")
    chosen = [_index(item, options, path) for item in value]
    if len(set(chosen)) != len(chosen):
        raise ResultError(path, "repeats an option")
    return sorted(chosen)


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ResultError(path, "is not text")
    return value


def _ask(spec: Spec, values: Mapping[str, Any]) -> dict[str, Any]:
    questions = spec.fields["questions"]
    expected: set[str] = set()
    active: set[int] = set()
    answers: dict[str, Any] = {}
    for index, question in enumerate(questions):
        condition = question.get("when")
        if condition is not None:
            source = condition["question"]
            if source not in active:
                continue
            # Earlier values were validated before reaching this condition.
            chosen = values[f"q{source}"]
            matches = (
                condition["option"] in chosen
                if condition["operator"] == "includes"
                else type(chosen) is int and chosen == condition["option"]
            )
            if not matches:
                continue
        active.add(index)
        keys = {f"q{index}"}
        if question["other"]:
            keys.add(f"q{index}.other")
        if not keys <= values.keys():
            raise ResultError("result.values", "does not match the dialog's inputs")
        expected |= keys
        path = f"questions[{index}]"
        raw = values[f"q{index}"]
        typed = _string(values[f"q{index}.other"], path).strip() if question["other"] else ""
        answer: Any
        if question["kind"] == "text":
            answer = _string(raw, path)
            if (
                "image" in question
                and len(answer.encode("utf-16-le", "surrogatepass")) // 2 > MAX_COMMENT_LENGTH
            ):
                raise ResultError(path, "exceeds the image comment length limit")
            missing = not answer.strip()
        elif question["kind"] == "choice":
            if raw == OTHER and question["other"]:
                answer, missing = (typed or None), not typed
            elif raw is None:
                answer, missing = None, True
            else:
                answer = question["options"][_index(raw, question["options"], path)]["id"]
                missing = False
        else:
            chosen = _indexes(raw, question["options"], path)
            answer = [question["options"][i]["id"] for i in chosen]
            if typed:
                answer.append(typed)
            missing = not answer
        if missing and question["required"]:
            raise ResultError(path, "has no answer")
        answers[question["id"]] = answer
    _keys(values, expected)
    return answers


def _form(spec: Spec, values: Mapping[str, Any]) -> dict[str, Any]:
    inputs = form_inputs(spec.fields["components"])
    _keys(values, {f"c{i}" for i in range(len(inputs))})
    answers: dict[str, Any] = {}
    for index, component in enumerate(inputs):
        path = f"inputs[{index}]"
        raw = values[f"c{index}"]
        answer: Any
        if component["type"] == "field":
            answer = _string(raw, path)
            missing = not answer.strip()
        elif component["type"] == "select":
            missing = raw is None
            answer = (
                None
                if missing
                else component["options"][_index(raw, component["options"], path)]["id"]
            )
        else:
            if not isinstance(raw, bool):
                raise ResultError(path, "is not true or false")
            answer, missing = raw, False
        if missing and component.get("required"):
            raise ResultError(path, "has no answer")
        answers[component["id"]] = answer
    return answers


def _secret(spec: Spec, values: Mapping[str, Any]) -> list[tuple[int, str]]:
    fields = spec.fields["fields"]
    _keys(values, {f"f{i}" for i in range(len(fields))})
    return [
        (index, _string(values[f"f{index}"], f"fields[{index}]")) for index in range(len(fields))
    ]
