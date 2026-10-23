"""The spec model: one verb and its parameters, validated without a web view.

Every refusal is value-free. A :class:`SpecError` carries a path built only
from schema names and indexes and a fixed problem phrase; it never quotes a
value from the spec, and it never names an unknown field.
"""

from __future__ import annotations

import json
import math
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fenric.html_bundle import MAX_FILES, is_html_entry, valid_member_name
from fenric.images import MAX_IMAGES, MAX_TOTAL_IMAGE_BYTES, validate_image

VERBS = ("notify", "confirm", "pick", "ask", "secret", "form", "custom", "chart", "html")
FORM_CATALOG_VERSION = 1
DEFAULT_APP = "Fenric"
MAX_TIMEOUT_SECONDS = 3600
DEFAULT_FRAME_HEIGHT = 420
MIN_FRAME_HEIGHT = 100
MAX_FRAME_HEIGHT = 2000

_COMMON_FIELDS = {"verb", "app", "timeout"}
# Each verb's own fields, beside the three every spec accepts.
VERB_FIELDS: dict[str, frozenset[str]] = {
    "notify": frozenset({"message", "title", "detail", "copy_text"}),
    "confirm": frozenset({"message", "title", "detail", "yes_label", "no_label"}),
    "pick": frozenset({"message", "title", "options", "multiple"}),
    "ask": frozenset({"message", "title", "questions", "presentation"}),
    "secret": frozenset({"message", "title", "fields", "store", "into", "store_label"}),
    "form": frozenset({"title", "catalog", "components"}),
    "custom": frozenset({"title", "html", "height"}),
    "chart": frozenset({"title", "code", "data", "height"}),
    "html": frozenset({"title", "root", "entry", "files", "height"}),
}
_SECRET_NAME = re.compile(r"[A-Za-z0-9._-]+\Z")
_QUESTION_KINDS = ("text", "choice", "multi")


class SpecError(ValueError):
    """A spec was refused. ``path`` names the field; nothing quotes its content."""

    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"{path}: {problem}")
        self.path = path
        self.problem = problem


@dataclass(frozen=True)
class Spec:
    """A validated, normalized request."""

    verb: str
    fields: dict[str, Any] = field(default_factory=dict)
    app: str = DEFAULT_APP
    timeout: float | None = None
    store: str | None = None
    into: str | None = None


def validate_spec(raw: object) -> Spec:
    """Validate ``raw`` and return its normalized form, or raise :class:`SpecError`."""
    spec = _mapping(raw, "spec")
    verb = spec.get("verb")
    if not isinstance(verb, str) or verb not in VERBS:
        raise SpecError("verb", "must be one of the supported verbs")
    app = _text(spec, "app", "app", default=DEFAULT_APP)
    timeout = _timeout(spec.get("timeout"))
    fields, store = _VERB_VALIDATORS[verb](spec)
    into = spec.get("into") if verb == "secret" else None
    return Spec(verb=verb, fields=fields, app=app, timeout=timeout, store=store, into=into)


def form_inputs(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a form's input components in document order."""
    found: list[dict[str, Any]] = []
    for component in components:
        if component["type"] in ("field", "select", "checkbox"):
            found.append(component)
        elif component["type"] == "card":
            found.extend(form_inputs(component["children"]))
    return found


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise SpecError(path, "must be an object")
    return value


def _only(obj: Mapping[str, Any], allowed: set[str], path: str) -> None:
    if not set(obj) <= allowed:
        raise SpecError(path, "has an unknown field")


def _text(
    obj: Mapping[str, Any],
    key: str,
    path: str,
    *,
    required: bool = False,
    default: str | None = None,
) -> Any:
    if key not in obj or obj[key] is None:
        if required:
            raise SpecError(path, "is required")
        return default
    value = obj[key]
    if not isinstance(value, str) or not value.strip():
        raise SpecError(path, "must be a non-empty string")
    return value


def _flag(obj: Mapping[str, Any], key: str, path: str, *, default: bool = False) -> bool:
    if key not in obj:
        return default
    if not isinstance(obj[key], bool):
        raise SpecError(path, "must be true or false")
    return obj[key]


def _timeout(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError("timeout", "must be a number of seconds")
    # Bounds first: an integer too large for a float cannot be tested for finiteness.
    if not 0 < value <= MAX_TIMEOUT_SECONDS or not math.isfinite(value):
        raise SpecError("timeout", "must be greater than 0 and at most 3600 seconds")
    return float(value)


def _items(obj: Mapping[str, Any], key: str, path: str) -> list[Any]:
    if key not in obj:
        raise SpecError(path, "is required")
    value = obj[key]
    if not isinstance(value, list) or not value:
        raise SpecError(path, "must be a non-empty list")
    return value


def _options(obj: Mapping[str, Any], key: str, path: str) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(_items(obj, key, path)):
        here = f"{path}[{index}]"
        if isinstance(item, str):
            if not item.strip():
                raise SpecError(here, "must be a non-empty string")
            option = {"id": item, "label": item, "detail": None}
        else:
            entry = _mapping(item, here)
            _only(entry, {"id", "label", "detail"}, here)
            label = _text(entry, "label", f"{here}.label", required=True)
            option = {
                "id": _text(entry, "id", f"{here}.id", default=label),
                "label": label,
                "detail": _text(entry, "detail", f"{here}.detail"),
            }
        if option["id"] in seen:
            raise SpecError(f"{here}.id", "duplicates an earlier id")
        seen.add(option["id"])
        options.append(option)
    return options


def _height(obj: Mapping[str, Any]) -> int:
    value = obj.get("height", DEFAULT_FRAME_HEIGHT)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpecError("height", "must be a whole number of pixels")
    if not MIN_FRAME_HEIGHT <= value <= MAX_FRAME_HEIGHT:
        raise SpecError("height", "must be between 100 and 2000 pixels")
    return value


def _notify(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["notify"], "spec")
    fields = {
        "message": _text(spec, "message", "message", required=True),
        "title": _text(spec, "title", "title"),
        "detail": _text(spec, "detail", "detail"),
    }
    if "copy_text" in spec:
        payload = _text(spec, "copy_text", "copy_text", required=True)
        try:
            payload.encode("utf-8")
        except UnicodeError as exc:
            raise SpecError("copy_text", "must be valid UTF-8 text") from exc
        fields["copy_text"] = payload
    return fields, None


def _confirm(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["confirm"], "spec")
    return {
        "message": _text(spec, "message", "message", required=True),
        "title": _text(spec, "title", "title"),
        "detail": _text(spec, "detail", "detail"),
        "yes_label": _text(spec, "yes_label", "yes_label", default="Yes"),
        "no_label": _text(spec, "no_label", "no_label", default="No"),
    }, None


def _pick(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["pick"], "spec")
    return {
        "message": _text(spec, "message", "message", required=True),
        "title": _text(spec, "title", "title"),
        "options": _options(spec, "options", "options"),
        "multiple": _flag(spec, "multiple", "multiple"),
    }, None


def _question(item: object, here: str, seen: set[str]) -> dict[str, Any]:
    entry = _mapping(item, here)
    _only(
        entry,
        {
            "id",
            "prompt",
            "detail",
            "kind",
            "options",
            "other",
            "multiline",
            "required",
            "when",
            "image",
        },
        here,
    )
    identifier = _text(entry, "id", f"{here}.id", required=True)
    if identifier in seen:
        raise SpecError(f"{here}.id", "duplicates an earlier id")
    seen.add(identifier)
    kind = entry.get("kind", "choice" if "options" in entry else "text")
    if kind not in _QUESTION_KINDS:
        raise SpecError(f"{here}.kind", "must be text, choice or multi")
    question: dict[str, Any] = {
        "id": identifier,
        "prompt": _text(entry, "prompt", f"{here}.prompt", required=True),
        "detail": _text(entry, "detail", f"{here}.detail"),
        "kind": kind,
        "options": [],
        "other": False,
        "multiline": False,
        "required": _flag(entry, "required", f"{here}.required", default=kind != "multi"),
    }
    if kind == "text":
        if "options" in entry or "other" in entry:
            raise SpecError(here, "a text question takes no options")
        question["multiline"] = _flag(entry, "multiline", f"{here}.multiline")
    else:
        if "multiline" in entry:
            raise SpecError(here, "only a text question may be multiline")
        question["options"] = _options(entry, "options", f"{here}.options")
        question["other"] = _flag(entry, "other", f"{here}.other")
    if "image" in entry:
        if kind != "text":
            raise SpecError(f"{here}.image", "requires a text question")
        try:
            question["image"] = validate_image(entry["image"])
        except ValueError as exc:
            raise SpecError(f"{here}.image", str(exc)) from None
    return question


def _ask(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["ask"], "spec")
    presentation = spec.get("presentation", "form")
    if presentation not in ("form", "wizard"):
        raise SpecError("presentation", "must be form or wizard")
    seen: set[str] = set()
    questions: list[dict[str, Any]] = []
    for index, item in enumerate(_items(spec, "questions", "questions")):
        here = f"questions[{index}]"
        question = _question(item, here, seen)
        if "when" in item:
            if presentation != "wizard":
                raise SpecError(f"{here}.when", "requires wizard presentation")
            condition = _mapping(item["when"], f"{here}.when")
            if set(condition) not in ({"question", "equals"}, {"question", "includes"}):
                raise SpecError(f"{here}.when", "must name a question and one comparison")
            source_id = _text(condition, "question", f"{here}.when.question", required=True)
            source_index = next(
                (i for i, prior in enumerate(questions) if prior["id"] == source_id), None
            )
            if source_index is None:
                raise SpecError(f"{here}.when.question", "must name an earlier question")
            source = questions[source_index]
            operator = "equals" if "equals" in condition else "includes"
            expected_kind = "choice" if operator == "equals" else "multi"
            if source["kind"] != expected_kind:
                raise SpecError(f"{here}.when", "comparison does not match the question kind")
            option_id = _text(condition, operator, f"{here}.when.{operator}", required=True)
            option_index = next(
                (i for i, option in enumerate(source["options"]) if option["id"] == option_id), None
            )
            if option_index is None:
                raise SpecError(f"{here}.when.{operator}", "must name an offered option")
            question["when"] = {
                "question": source_index,
                "option": option_index,
                "operator": operator,
            }
        questions.append(question)
    images = [question["image"] for question in questions if "image" in question]
    if (
        len(images) > MAX_IMAGES
        or sum(image["bytes"] for image in images) > MAX_TOTAL_IMAGE_BYTES
        or sum(image["width"] * image["height"] for image in images) > 24_000_000
    ):
        raise SpecError("questions", "exceeds the image count or total size limit")
    return {
        "message": _text(spec, "message", "message"),
        "title": _text(spec, "title", "title"),
        "questions": questions,
        "presentation": presentation,
    }, None


def _secret(spec: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["secret"], "spec")
    if "store_label" in spec and "store" not in spec:
        raise SpecError("store_label", "requires a store command")
    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(_items(spec, "fields", "fields")):
        here = f"fields[{index}]"
        entry = {"name": item} if isinstance(item, str) else _mapping(item, here)
        _only(entry, {"name", "label"}, here)
        name = _text(entry, "name", f"{here}.name", required=True)
        if not _SECRET_NAME.match(name):
            raise SpecError(
                f"{here}.name", "may hold only letters, digits, dot, underscore and hyphen"
            )
        if name in seen:
            raise SpecError(f"{here}.name", "duplicates an earlier name")
        seen.add(name)
        fields.append({"name": name, "label": _text(entry, "label", f"{here}.label", default=name)})
    return {
        "message": _text(spec, "message", "message"),
        "title": _text(spec, "title", "title"),
        "fields": fields,
        "store_label": _text(spec, "store_label", "store_label"),
    }, _store(spec)


def _store(spec: Mapping[str, Any]) -> str | None:
    """Check the secret verb's one destination; return the store command if that is it."""
    store, into = spec.get("store"), spec.get("into")
    if store is None and into is None:
        raise SpecError(
            "store", "or into is required: the secret verb needs a store command or a file"
        )
    if store is not None and into is not None:
        raise SpecError("into", "cannot be given together with store")
    if into is not None:
        if not isinstance(into, str) or not into or "\x00" in into:
            raise SpecError("into", "must be a file path")
        return None
    if not isinstance(store, str):
        raise SpecError("store", "must be a command string")
    try:
        parts = shlex.split(store)
    except ValueError:
        parts = []
    if not parts or "\x00" in store:
        raise SpecError("store", "must be a command string")
    return store


def _component(item: object, here: str, seen: set[str]) -> dict[str, Any]:
    entry = _mapping(item, here)
    kind = entry.get("type")
    if not isinstance(kind, str) or kind not in _COMPONENTS:
        raise SpecError(f"{here}.type", "is not in the form catalog")
    allowed, build = _COMPONENTS[kind]
    _only(entry, allowed | {"type"}, here)
    component = build(entry, here, seen)
    component["type"] = kind
    if "id" in component:
        if component["id"] in seen:
            raise SpecError(f"{here}.id", "duplicates an earlier id")
        seen.add(component["id"])
    return component


def _components(obj: Mapping[str, Any], key: str, path: str, seen: set[str]) -> list[Any]:
    return [
        _component(item, f"{path}[{index}]", seen)
        for index, item in enumerate(_items(obj, key, path))
    ]


def _heading(entry: Mapping[str, Any], here: str, seen: set[str]) -> dict[str, Any]:
    level = entry.get("level", 2)
    if type(level) is not int or level not in (1, 2, 3):
        raise SpecError(f"{here}.level", "must be 1, 2 or 3")
    return {"text": _text(entry, "text", f"{here}.text", required=True), "level": level}


def _paragraph(entry: Mapping[str, Any], here: str, seen: set[str]) -> dict[str, Any]:
    return {"text": _text(entry, "text", f"{here}.text", required=True)}


def _field(entry: Mapping[str, Any], here: str, seen: set[str]) -> dict[str, Any]:
    return {
        "id": _text(entry, "id", f"{here}.id", required=True),
        "label": _text(entry, "label", f"{here}.label", required=True),
        "placeholder": _text(entry, "placeholder", f"{here}.placeholder"),
        "multiline": _flag(entry, "multiline", f"{here}.multiline"),
        "required": _flag(entry, "required", f"{here}.required"),
    }


def _select(entry: Mapping[str, Any], here: str, seen: set[str]) -> dict[str, Any]:
    return {
        "id": _text(entry, "id", f"{here}.id", required=True),
        "label": _text(entry, "label", f"{here}.label", required=True),
        "options": _options(entry, "options", f"{here}.options"),
        "required": _flag(entry, "required", f"{here}.required"),
    }


def _checkbox(entry: Mapping[str, Any], here: str, seen: set[str]) -> dict[str, Any]:
    return {
        "id": _text(entry, "id", f"{here}.id", required=True),
        "label": _text(entry, "label", f"{here}.label", required=True),
        "checked": _flag(entry, "checked", f"{here}.checked"),
    }


def _card(entry: Mapping[str, Any], here: str, seen: set[str]) -> dict[str, Any]:
    return {
        "title": _text(entry, "title", f"{here}.title"),
        "children": _components(entry, "children", f"{here}.children", seen),
    }


def _list(entry: Mapping[str, Any], here: str, seen: set[str]) -> dict[str, Any]:
    items = _items(entry, "items", f"{here}.items")
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise SpecError(f"{here}.items[{index}]", "must be a non-empty string")
    return {"items": list(items), "ordered": _flag(entry, "ordered", f"{here}.ordered")}


_Builder = Callable[[Mapping[str, Any], str, set[str]], dict[str, Any]]
_COMPONENTS: dict[str, tuple[set[str], _Builder]] = {
    "heading": ({"text", "level"}, _heading),
    "text": ({"text"}, _paragraph),
    "field": ({"id", "label", "placeholder", "multiline", "required"}, _field),
    "select": ({"id", "label", "options", "required"}, _select),
    "checkbox": ({"id", "label", "checked"}, _checkbox),
    "card": ({"title", "children"}, _card),
    "list": ({"items", "ordered"}, _list),
}


def _form(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["form"], "spec")
    catalog = spec.get("catalog", FORM_CATALOG_VERSION)
    if type(catalog) is not int or catalog != FORM_CATALOG_VERSION:
        raise SpecError("catalog", "names a form catalog version this Fenric does not support")
    components = _components(spec, "components", "components", set())
    return {"title": _text(spec, "title", "title"), "components": components}, None


def _custom(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["custom"], "spec")
    return {
        "title": _text(spec, "title", "title"),
        "html": _text(spec, "html", "html", required=True),
        "height": _height(spec),
    }, None


def _chart(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["chart"], "spec")
    try:
        json.dumps(spec.get("data"), allow_nan=False)
    except (TypeError, ValueError):
        raise SpecError("data", "must be JSON data") from None
    return {
        "title": _text(spec, "title", "title"),
        "code": _text(spec, "code", "code", required=True),
        "data": spec.get("data"),
        "height": _height(spec),
    }, None


def _html(spec: Mapping[str, Any]) -> tuple[dict[str, Any], None]:
    _only(spec, _COMMON_FIELDS | VERB_FIELDS["html"], "spec")
    root = _text(spec, "root", "root", required=True)
    entry = _text(spec, "entry", "entry", required=True)
    files = _items(spec, "files", "files")
    if len(files) > MAX_FILES or not all(valid_member_name(item) for item in files):
        raise SpecError("files", "must be a bounded list of relative presentation files")
    if len(set(files)) != len(files) or entry not in files:
        raise SpecError("files", "must be unique and include the entry")
    if "\0" in root:
        raise SpecError("root", "must not contain NUL")
    if not is_html_entry(entry):
        raise SpecError("entry", "must select an HTML file")
    return {
        "title": _text(spec, "title", "title"),
        "root": root,
        "entry": entry,
        "files": list(files),
        "height": _height(spec),
    }, None


_VERB_VALIDATORS: dict[str, Callable[[Mapping[str, Any]], tuple[dict[str, Any], str | None]]] = {
    "notify": _notify,
    "confirm": _confirm,
    "pick": _pick,
    "ask": _ask,
    "secret": _secret,
    "form": _form,
    "custom": _custom,
    "chart": _chart,
    "html": _html,
}
