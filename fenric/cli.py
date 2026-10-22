"""Command-line entry point for Fenric.

One invocation renders one window, collects one response, and exits:

    fenric <verb> [options]
    fenric --spec '<json>'
    fenric --spec -
    fenric --spec-file path.json

Exit status 0 means the person answered and stdout holds the result, 2 that
they cancelled, 3 that the request was invalid or could not be carried out,
and 4 that no window was available. Diagnostics go to stderr and never quote
a value from the spec, from the person, or from the command line.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from fenric import __version__, remote
from fenric.charts import ChartError, setup_charts
from fenric.engine import EXIT_INVALID, EXIT_OK, Result, run
from fenric.gallery import load_gallery
from fenric.interaction import run_integrated
from fenric.session import CallerSignals
from fenric.spec import VERBS, SpecError

SETUP_CHARTS = "setup-charts"
_USAGE_ERROR = "invalid invocation; run `fenric --help`"

# Verb option -> spec field, for the options that carry one scalar value.
_SCALAR_OPTIONS = {
    "notify": ("message", "title", "detail", "copy_text"),
    "confirm": ("message", "title", "detail", "yes_label", "no_label"),
    "pick": ("message", "title"),
    "ask": ("message", "title"),
    "secret": ("message", "title", "store", "into", "store_label"),
    "form": ("title",),
    "custom": ("title", "html", "height"),
    "chart": ("title", "height"),
}
_HELP = {
    "notify": "Show a message. Prints ok.",
    "confirm": "Ask yes or no. Prints true or false.",
    "pick": "Choose one or several from a list. Prints the choice.",
    "ask": (
        "Pose one or more questions. Prints an object keyed by question id. "
        "For several decisions, use --spec-file with an ask spec "
        'containing presentation: "wizard". '
        "Omitting presentation shows all questions together; there is no wizard verb."
    ),
    "secret": "Collect credentials into a store command or a file. Prints ok.",
    "form": "Show a form from the component catalog. Prints an object keyed by input id.",
    "custom": "Show sanitized HTML in an inert frame. Prints ok.",
    "chart": "Show a d3 chart written in TypeScript. Prints ok.",
}


class _Refusal(Exception):
    """The invocation cannot be carried out; the message is value-free."""


class _Parser(argparse.ArgumentParser):
    """An argument parser whose usage errors never echo an argument."""

    def error(self, message: str) -> NoReturn:
        raise _Refusal(_USAGE_ERROR)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every verb and invocation form."""
    common = _Parser(add_help=False)
    absent = argparse.SUPPRESS
    common.add_argument("--spec", default=absent, help="the spec as JSON, or - to read stdin")
    common.add_argument("--spec-file", default=absent, help="a file holding the spec as JSON")
    common.add_argument("--app", default=absent, help="name of the program that is asking")
    common.add_argument(
        "--timeout", type=float, default=absent, help="seconds before an untouched dialog expires"
    )
    common.add_argument(
        "--fallback-file",
        default=absent,
        help="where to write the request as plain text when no window is available",
    )

    common.add_argument("--desktop-only", action="store_true", default=absent)
    common.add_argument("--remote-status-file", default=absent)

    parser = _Parser(
        prog="fenric",
        parents=[common],
        allow_abbrev=False,
        description=(
            "Put a native dialog in front of the person at the machine and "
            "return what they answered as validated JSON or a plain status."
        ),
        epilog=(
            "Exit status: 0 answered, 2 cancelled, 3 invalid request, 4 unavailable. "
            "Agent usage guide: fenric guidance read (read-only; no setup)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"fenric {__version__}")
    verbs = parser.add_subparsers(dest="verb", metavar="<verb>")

    def verb(name: str) -> argparse.ArgumentParser:
        return verbs.add_parser(
            name, parents=[common], allow_abbrev=False, help=_HELP[name], description=_HELP[name]
        )

    def text(target: argparse.ArgumentParser, *flags: str) -> None:
        for flag in flags:
            target.add_argument(flag, default=absent)

    notify = verb("notify")
    text(notify, "--message", "--title", "--detail")
    copying = notify.add_mutually_exclusive_group()
    copying.add_argument("--copy-text", default=absent, help="exact plain text to display and copy")
    copying.add_argument(
        "--copy-file", default=absent, help="UTF-8 payload file; preserves newlines"
    )
    text(verb("confirm"), "--message", "--title", "--detail", "--yes-label", "--no-label")
    pick = verb("pick")
    text(pick, "--message", "--title")
    pick.add_argument("--option", action="append", default=absent, help="a choice; repeat")
    pick.add_argument("--multiple", action="store_true", default=absent)
    ask = verb("ask")
    text(ask, "--message", "--title")
    ask.add_argument(
        "--question", action="append", default=absent, help="a free-text question; repeat"
    )
    text(ask, "--gallery-file")
    secret = verb("secret")
    text(secret, "--message", "--title", "--store-label")
    secret.add_argument(
        "--field", action="append", default=absent, help="NAME or NAME=LABEL; repeat"
    )
    secret.add_argument(
        "--store", default=absent, help="command run once per filled field; {name} is the field"
    )
    secret.add_argument(
        "--into",
        default=absent,
        help="file each filled field is written into, in place of --store",
    )
    text(verb("form"), "--title")
    custom = verb("custom")
    text(custom, "--title", "--html", "--html-file")
    custom.add_argument("--height", type=int, default=absent)
    chart = verb("chart")
    text(chart, "--title", "--code-file", "--data-file")
    chart.add_argument("--height", type=int, default=absent)
    integrated = verbs.add_parser(
        "interact",
        allow_abbrev=False,
        help="Answer a host-admitted request on an inherited private channel.",
        description=(
            "Use an already admitted private Unix socket; this flag grants no native authority."
        ),
    )
    integrated.add_argument("--channel-fd", type=int, required=True)
    verbs.add_parser(
        SETUP_CHARTS,
        allow_abbrev=False,
        help="Provision the chart toolchain into the per-user cache. Prints ok.",
    )
    machine = verbs.add_parser("remote", allow_abbrev=False, help="Configure the tailnet surface.")
    commands = machine.add_subparsers(dest="remote_command", required=True)
    configure = commands.add_parser("setup", allow_abbrev=False)
    configure.add_argument("--tailscale", default="tailscale")
    configure.add_argument("--port-start", type=int, default=8443)
    configure.add_argument("--port-end", type=int, default=8450)
    configure.add_argument("--grace", type=float, default=3)
    configure.add_argument("--announce")
    commands.add_parser("status", allow_abbrev=False)
    commands.add_parser("off", allow_abbrev=False)
    guidance_parser = verbs.add_parser(
        "guidance",
        allow_abbrev=False,
        help="Read agent usage guidance or review instruction setup.",
    )
    from fenric.guidance import configure_parser

    configure_parser(guidance_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one invocation and return its exit status."""
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return EXIT_OK
    try:
        args = parser.parse_args(arguments)
        if args.verb == "guidance":
            from fenric.guidance import execute

            allowed = {"verb", "guidance_command", "home", "plan", "remove", "backup_dir", "skill"}
            if set(vars(args)) - allowed:
                raise _Refusal(_USAGE_ERROR)
            return execute(args)
        if args.verb == "interact":
            if set(vars(args)) != {"verb", "channel_fd"} or args.channel_fd < 3:
                raise _Refusal(_USAGE_ERROR)
            try:
                with socket.socket(fileno=args.channel_fd) as channel, CallerSignals() as signals:
                    outcome = run_integrated(channel, cancel=signals.cancel)
            except (OSError, ValueError):
                raise _Refusal("integrated interaction failed") from None
            if outcome.collection == "error":
                print("fenric: error: integrated interaction failed", file=sys.stderr)
            return {
                "answered": 0,
                "cancelled": 2,
                "error": 3,
                "expired": 4,
                "superseded": 4,
                "unavailable": 4,
            }[outcome.collection]
        if args.verb == "remote":
            try:
                if args.remote_command == "setup":
                    remote.setup(
                        tailscale=args.tailscale,
                        port_start=args.port_start,
                        port_end=args.port_end,
                        grace=args.grace,
                        announce=args.announce,
                    )
                    print("ok")
                elif args.remote_command == "off":
                    remote.off()
                    print("ok")
                else:
                    config = remote.read_config()
                    print(json.dumps(config or {"enabled": False}, separators=(",", ":")))
                return EXIT_OK
            except remote.RemoteError as exc:
                raise _Refusal(str(exc)) from None
        if args.verb == SETUP_CHARTS:
            try:
                setup_charts()
            except ChartError as exc:
                raise _Refusal(str(exc)) from exc
            print("ok")
            return EXIT_OK
        spec = _assemble(vars(args))
    except (_Refusal, SpecError) as refusal:
        print(f"fenric: error: {refusal}", file=sys.stderr)
        return EXIT_INVALID
    with CallerSignals() as signals:
        result = run(
            spec,
            fallback_file=getattr(args, "fallback_file", None),
            desktop_only=getattr(args, "desktop_only", False),
            status_file=getattr(args, "remote_status_file", None),
            cancel=signals.cancel,
        )
    return _report(result)


def _report(result: Result) -> int:
    if result.status == "ok":
        if result.value is None:
            print("ok")
        else:
            print(json.dumps(result.value, ensure_ascii=False, separators=(",", ":")))
    elif result.status == "cancelled":
        print("cancelled")
    elif result.status == "unavailable":
        print(f"unavailable: {result.detail}")
    else:
        print(f"fenric: error: {result.detail}", file=sys.stderr)
    return result.exit_code


def _read(path: str, what: str) -> str:
    try:
        if path == "-":
            return sys.stdin.read()
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError) as exc:
        raise _Refusal(f"the {what} could not be read") from exc


def _json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise _Refusal(f"the {what} is not valid JSON") from exc


def _assemble(options: dict[str, Any]) -> dict[str, Any]:
    """Build the spec from a JSON source, verb options, or both."""
    verb = options.get("verb")
    if "gallery_file" in options and any(
        key in options for key in ("spec", "spec_file", "question")
    ):
        raise _Refusal("give a gallery manifest without other question specifications")
    if "spec" in options and "spec_file" in options:
        raise _Refusal("give the spec once: --spec or --spec-file")
    if "gallery_file" in options:
        spec = load_gallery(options["gallery_file"])
    elif "spec" in options:
        text = _read("-", "spec") if options["spec"] == "-" else options["spec"]
        spec = _json(text, "spec")
    elif "spec_file" in options:
        spec = _json(_read(options["spec_file"], "spec file"), "spec file")
    elif verb is None:
        raise _Refusal(_USAGE_ERROR)
    else:
        spec = {}
    if not isinstance(spec, dict):
        raise _Refusal("the spec must be a JSON object")
    if verb is not None:
        if spec.get("verb", verb) != verb:
            raise _Refusal("the spec names a different verb than the command")
        spec["verb"] = verb
    for key in ("app", "timeout"):
        if key in options:
            spec[key] = options[key]
    if verb not in VERBS:
        return spec

    for key in _SCALAR_OPTIONS[verb]:
        if key in options:
            spec[key] = options[key]
    if "copy_file" in options:
        if "copy_text" in spec:
            raise _Refusal("give the copy payload once")
        try:
            spec["copy_text"] = Path(options["copy_file"]).read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise _Refusal("the copy file could not be read") from exc
    if "option" in options:
        spec["options"] = list(options["option"])
    if "multiple" in options:
        spec["multiple"] = True
    if "question" in options:
        spec["questions"] = [
            {"id": f"q{number}", "prompt": prompt}
            for number, prompt in enumerate(options["question"], 1)
        ]
    if "field" in options:
        spec["fields"] = [
            dict(zip(("name", "label"), entry.split("=", 1), strict=False))
            for entry in options["field"]
        ]
    if "html_file" in options:
        spec["html"] = _read(options["html_file"], "html file")
    if "code_file" in options:
        spec["code"] = _read(options["code_file"], "code file")
    if "data_file" in options:
        spec["data"] = _json(_read(options["data_file"], "data file"), "data file")
    return spec


if __name__ == "__main__":
    sys.exit(main())
