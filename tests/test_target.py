"""Secrets written into a file: format detection, in-place update, refusal."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from fenric import target
from fenric.target import TargetError, check_target, write_target


def put(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_bytes(content.encode("utf-8"))
    path.chmod(mode)
    return path


def update(path: Path, **values: str) -> str:
    names = list(values)
    check_target(str(path), names)
    write_target(str(path), names, list(enumerate(values.values())))
    return path.read_bytes().decode("utf-8")


def refusal(path: Path, **values: str) -> str:
    """Return why an update is refused; the target and its directory must be as they were."""
    before = (path.read_bytes(), path.stat().st_mode) if path.is_file() else None
    with pytest.raises(TargetError) as raised:
        update(path, **values)
    assert ((path.read_bytes(), path.stat().st_mode) if path.is_file() else None) == before
    assert not [entry for entry in path.parent.iterdir() if entry.name.startswith(f".{path.name}.")]
    return str(raised.value)


def bash_loads(path: Path, *names: str) -> list[str]:
    """Return what the real shell holds in each variable after loading the file."""
    script = '. "$1"; shift; for name in "$@"; do printf "%s\\0" "${!name}"; done'
    loaded = subprocess.run(
        ["/bin/bash", "-c", script, "bash", str(path), *names],
        capture_output=True,
        check=True,
        timeout=20,
    )
    return loaded.stdout.decode().split("\0")[:-1]


def test_an_existing_dotenv_key_is_replaced_and_every_other_byte_kept(tmp_path: Path) -> None:
    before = (
        "# keys\r\nFIRST=1\r\n\r\n"
        "  export  SECOND = 'old one'   # rotate monthly\r\n"
        'THIRD="3"\r\n'
    )
    after = update(put(tmp_path / "service.env", before), SECOND="new", FOURTH="4")
    assert after == before.replace("'old one'", "new") + "FOURTH=4\r\n"


def test_a_direnv_file_is_updated_with_export_lines(tmp_path: Path) -> None:
    before = '# loaded by the shell\n\nexport FIRST=1\nexport SECOND="two" # note\nexport THIRD=3'
    after = update(put(tmp_path / ".envrc", before), SECOND="new", FOURTH="4")
    assert after == before.replace('"two"', "new") + "\nexport FOURTH=4\n"


def test_an_envrc_keeps_its_simple_helper_lines_and_refuses_anything_more(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".envrc"
    helpers = (
        "source_up\nPATH_add bin\nexport FIRST=1\nlayout python3\ndotenv_if_exists .env.local\n"
    )
    assert update(put(path, helpers), FIRST="x", SECOND="2") == (
        helpers.replace("FIRST=1", "FIRST=x") + "export SECOND=2\n"
    )
    # Only helper lines: the file is still a shell file, written with export.
    assert update(put(path, "use flake\n"), FIRST="1") == "use flake\nexport FIRST=1\n"
    # A line the shell would read as more than a word and its arguments.
    for line in ('eval "$(lorri direnv)"', "watch_file a; echo b", "PATH_add $HOME/bin", "x && y"):
        assert refusal(put(path, f"export FIRST=1\n{line}\n"), FIRST="x") == target.UNSUPPORTED
        # On its own, such a line is not taken for a single value either.
        assert refusal(put(path, f"{line}\n"), FIRST="x") == target.UNSUPPORTED
    # A compound command, or a command that declares or removes a variable,
    # can decide what the assignments around it do: never a simple helper.
    compound = (
        "if false\nthen\nexport FIRST=1\nfi\n",
        "source_up\nif true\nthen\n",
        "readonly FIRST=1\n",
        "export OTHER FIRST=1\n",
        "export FIRST=1\nunset FIRST\n",
    )
    for text in compound:
        assert refusal(put(path, text), FIRST="x") == target.UNSUPPORTED
    # The shell reads `echo = old` as a command, not as a key called echo.
    command = "echo = old\nexport FIRST=1\n"
    assert update(put(path, command), echo="new") == command + "export echo=new\n"
    # A link named .envrc is read as a shell file whatever its file is called.
    backing = put(tmp_path / "rc-data", 'eval "$(lorri direnv)"\n')
    link = tmp_path / "linked" / ".envrc"
    link.parent.mkdir()
    link.symlink_to(backing)
    with pytest.raises(TargetError, match=target.UNSUPPORTED):
        write_target(str(link), ["FIRST"], [(0, "x")])
    assert backing.read_text() == 'eval "$(lorri direnv)"\n' and link.is_symlink()
    # Elsewhere a helper line is still not a dotenv line.
    other = put(tmp_path / ".env", "FIRST=1\nPATH_add bin\n")
    assert refusal(other, FIRST="x") == target.UNSUPPORTED


def test_a_dotenv_value_is_written_only_in_a_form_every_reader_agrees_on(tmp_path: Path) -> None:
    path = put(tmp_path / ".env", "")
    values = {
        "BARE": "a-b_c.d:/e@f%g+h=i,j^k",
        "TILDE": "~",
        "SPACED": "two words # $x \\n",
        "APOSTROPHE": "it's 100%",
        "EMPTY": "",
    }
    assert update(path, **values) == (
        "BARE=a-b_c.d:/e@f%g+h=i,j^k\n"
        "TILDE='~'\n"
        "SPACED='two words # $x \\n'\n"
        'APOSTROPHE="it\'s 100%"\n'
        "EMPTY=''\n"
    )
    assert bash_loads(path, *values) == list(values.values())
    # What was written is recognised and replaced whole on the next update.
    assert update(path, SPACED="plain").splitlines()[2] == "SPACED=plain"
    problem = (
        "fields[1]: the value cannot be written so that"
        " every reader of the target file loads it unchanged"
    )
    for value in (
        "it's $5",
        'it\'s "quoted"',
        "it's a\\b",
        "${HOME} here",
        "two\\\\ back",
        "ends \\",
    ):
        assert refusal(path, FINE="ok", HARD=value) == problem


def test_a_shell_file_holds_any_one_line_value_as_the_shell_reads_it(tmp_path: Path) -> None:
    values = {
        "PLAIN": "abc-123",
        "TILDE": "~/x",
        "QUOTES": 'it\'s "$HOME" `id` \\ ${X} #no',
        "EMPTY": "",
    }
    path = put(tmp_path / ".envrc", "export PLAIN=old\n")
    written = update(path, **values)
    assert written.splitlines()[2] == "export QUOTES='it'\\''s \"$HOME\" `id` \\ ${X} #no'"
    assert bash_loads(path, *values) == list(values.values())
    # The form written for a single quote is one Fenric can replace next time.
    assert update(path, QUOTES="rotated").splitlines()[2] == "export QUOTES=rotated"


def test_an_existing_value_whose_end_is_unclear_is_refused_by_index(tmp_path: Path) -> None:
    problem = (
        "fields[1]: the value the target file holds is in a form that cannot be replaced safely"
    )
    for line in ("HARD=one two", "HARD=a#b", "HARD='a'b", "HARD=1\x0bB=2", "HARD='a' b"):
        path = put(tmp_path / ".env", f"FINE=1\n{line}\n")
        assert refusal(path, FINE="x", HARD="y") == problem
    for line in ("export HARD=old; echo note", "export HARD=$(op read x)", "export HARD=a&b"):
        path = put(tmp_path / ".envrc", f"export FINE=1\n{line}\n")
        assert refusal(path, FINE="x", HARD="y") == problem
        # The same line is no obstacle to writing a different key.
        assert update(path, FINE="x") == f"export FINE=x\n{line}\n"
    # A dotenv loader reads these characters as part of the value.
    assert update(put(tmp_path / ".env", "URL=db://h/n?a=1&b=$c\n"), URL="new") == "URL=new\n"


def test_a_flat_json_member_is_replaced_or_appended_as_text(tmp_path: Path) -> None:
    spread = put(
        tmp_path / "keys.json", '{\n    "first" : "1",\n    "tricky\\": \\"" : "\\u00e9"\n}\n'
    )
    assert update(spread, first='a"b\nc', added="ü") == (
        '{\n    "first" : "a\\"b\\nc",\n'
        '    "tricky\\": \\"" : "\\u00e9",\n    "added" : "\\u00fc"\n}\n'
    )
    compact = put(tmp_path / "compact.json", '{"first":"é"}')
    assert update(compact, second="ü") == '{"first":"é","second":"ü"}'
    assert update(put(tmp_path / "empty.json", "{}"), first="1") == '{\n  "first": "1"\n}'
    assert update(put(tmp_path / "blank.json", ""), first="1") == '{\n  "first": "1"\n}\n'


def test_json_that_is_not_a_flat_object_of_strings_is_refused(tmp_path: Path) -> None:
    flat = "the target file holds JSON that is not a flat object of strings"
    path = tmp_path / "token.json"
    assert refusal(put(path, '{"token": {"access": "1", "refresh": "2"}}'), token="x") == flat
    assert refusal(put(path, '{"count": 1}'), count="x") == flat
    assert refusal(put(path, '["first"]'), first="x") == flat
    assert refusal(put(path, '{"same": "1", "same": "2"}'), other="x") == target.UNSUPPORTED
    assert refusal(put(path, '{"open": "1"'), open="x") == target.UNSUPPORTED


def test_a_single_value_file_is_replaced_for_one_field_only(tmp_path: Path) -> None:
    assert update(put(tmp_path / "token", "old+token/==\n"), anything="new token") == "new token\n"
    assert update(put(tmp_path / "bare", "old"), anything="new") == "new"
    assert refusal(put(tmp_path / "token", "old\n"), first="1", second="2") == (
        "the target file holds a single value and the request has more than one field"
    )


def test_a_files_own_line_ending_is_kept_even_a_bare_carriage_return(tmp_path: Path) -> None:
    assert update(put(tmp_path / ".env", "FIRST=old\rSECOND=2\r"), FIRST="new", THIRD="3") == (
        "FIRST=new\rSECOND=2\rTHIRD=3\r"
    )
    assert update(put(tmp_path / "token", "old\r"), ONLY="new") == "new\r"
    assert update(put(tmp_path / "token", "old\r\n"), ONLY="new") == "new\r\n"


def test_a_one_key_assignment_is_dotenv_whatever_the_file_is_called(tmp_path: Path) -> None:
    assert update(put(tmp_path / "credentials", "API_KEY=old\n"), API_KEY="new") == "API_KEY=new\n"
    assert update(put(tmp_path / "credentials", "API_KEY=old\n"), OTHER="new") == (
        "API_KEY=old\nOTHER=new\n"
    )


def test_an_unsupported_file_is_refused_without_being_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".envrc"
    assert refusal(put(path, 'export FIRST=1\neval "$(lorri direnv)"\n'), FIRST="x") == (
        target.UNSUPPORTED
    )
    # Free text is refused; in an .envrc these lines would read as two commands.
    free = put(tmp_path / "notes.txt", "first line\nsecond line\n")
    assert refusal(free, FIRST="x") == target.UNSUPPORTED
    assert refusal(put(path, "[section]\nFIRST=1\n"), FIRST="x") == target.UNSUPPORTED
    assert refusal(put(path, '"FIRST"=1\nSECOND=2\n'), FIRST="x") == target.UNSUPPORTED
    assert refusal(put(path, "\ufeffFIRST=1\nSECOND=2\n"), FIRST="x") == target.UNSUPPORTED
    assert refusal(put(path, "FIRST=1\x00\n"), FIRST="x") == target.UNSUPPORTED
    # A quoted value that runs over several lines can hold lines that look like keys.
    continued = 'FIRST="one\nSECOND=two\nTHIRD=three"\n'
    assert refusal(put(path, continued), SECOND="x") == target.UNSUPPORTED
    assert refusal(put(path, "FIRST='one\nSECOND=two'\n"), SECOND="x") == target.UNSUPPORTED
    assert refusal(put(path, "FIRST=one \\\nSECOND=two\n"), SECOND="x") == target.UNSUPPORTED
    assert refusal(put(tmp_path / "deep.json", "[" * 100_000), FIRST="x") == target.UNSUPPORTED
    path.write_bytes(b"FIRST=\xff\n")
    assert refusal(path, FIRST="x") == target.UNSUPPORTED
    notes = put(tmp_path / "notes", "# nothing here says what this is\n")
    assert refusal(notes, FIRST="x") == target.UNSUPPORTED
    monkeypatch.setattr(target, "MAX_TARGET_BYTES", 8)
    assert refusal(put(path, "FIRST=123\n"), FIRST="x") == target.UNSUPPORTED


def test_a_target_that_is_not_a_regular_file_is_refused_without_being_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipe = tmp_path / ".env"
    os.mkfifo(pipe)

    def opened(*arguments: object, **options: object) -> None:
        raise AssertionError("the target was opened")

    # Opening a pipe with no writer would block for ever; the refusal comes
    # first, and a regression fails here instead of hanging the suite.
    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(TargetError, match=target.UNSUPPORTED):
        check_target(str(pipe), ["FIRST"])
    with pytest.raises(TargetError, match=target.UNSUPPORTED):
        check_target(str(tmp_path), ["FIRST"])
    loop = tmp_path / "loop.env"
    loop.symlink_to(tmp_path / "back.env")
    (tmp_path / "back.env").symlink_to(loop)
    with pytest.raises(TargetError, match=r"\Athe target file could not be read\Z"):
        check_target(str(loop), ["FIRST"])


def test_a_missing_target_is_created_owner_only_when_its_name_says_how(tmp_path: Path) -> None:
    created = {
        "new.env": "FIRST=1\n",
        ".envrc": "export FIRST=1\n",
        "new.json": '{\n  "FIRST": "1"\n}\n',
    }
    for name, content in created.items():
        path = tmp_path / name
        check_target(str(path), ["FIRST"])
        assert not path.exists()
        assert update(path, FIRST="1") == content
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert refusal(tmp_path / "token", FIRST="1") == (
        "the target file does not exist and its name does not say which format to create"
    )
    with pytest.raises(TargetError, match="the target file's directory does not exist"):
        check_target(str(tmp_path / "absent" / ".env"), ["FIRST"])
    assert not (tmp_path / "absent").exists()


def test_an_existing_targets_mode_and_link_are_kept(tmp_path: Path) -> None:
    real = put(tmp_path / "real.env", "FIRST=1\n", mode=0o644)
    link = tmp_path / "link.env"
    link.symlink_to(real)
    assert update(link, FIRST="2") == "FIRST=2\n"
    assert link.is_symlink() and not real.is_symlink()
    assert stat.S_IMODE(real.stat().st_mode) == 0o644


def test_a_key_the_file_holds_twice_is_refused_only_when_it_is_written(tmp_path: Path) -> None:
    path = put(tmp_path / ".env", "TWICE=1\nexport TWICE=2\nONCE=1\n")
    assert refusal(path, ONCE="x", TWICE="y") == (
        "fields[1].name appears more than once as a key in the target file"
    )
    assert update(path, ONCE="x") == "TWICE=1\nexport TWICE=2\nONCE=x\n"


def test_a_field_name_the_format_cannot_hold_is_refused_by_index(tmp_path: Path) -> None:
    problem = "fields[1].name cannot be a key in the target file's format"
    assert refusal(put(tmp_path / ".env", "FIRST=1\n"), FIRST="x", **{"api-key": "y"}) == problem
    assert update(put(tmp_path / "a.json", "{}"), **{"api-key": "y"}) == '{\n  "api-key": "y"\n}'


def test_a_value_with_a_line_break_is_refused_where_a_line_holds_the_value(
    tmp_path: Path,
) -> None:
    problem = "fields[{}]: the value holds a line break or a NUL, which the target file cannot hold"
    for value in ("a\nb", "a\rb", "a\x00b"):
        dotenv = put(tmp_path / ".env", "FIRST=1\n")
        assert refusal(dotenv, FIRST="fine", SECOND=value) == problem.format(1)
        assert refusal(put(tmp_path / "token", "old\n"), ONLY=value) == problem.format(0)
    # JSON holds all three, escaped.
    stored = update(put(tmp_path / "a.json", "{}"), ALL="a\nb\rc\x00d")
    assert stored == '{\n  "ALL": "a\\nb\\rc\\u0000d"\n}'
    unpaired = "fields[1]: the value is not valid text"
    assert refusal(put(tmp_path / "a.json", "{}"), FINE="ok", HALF="\ud800") == unpaired


def test_the_target_is_read_again_when_it_is_written(tmp_path: Path) -> None:
    path = put(tmp_path / ".env", "FIRST=1\n")
    check_target(str(path), ["FIRST"])
    put(path, "OTHER=9\nFIRST=1\n")
    write_target(str(path), ["FIRST"], [(0, "2")])
    assert path.read_text() == "OTHER=9\nFIRST=2\n"
    put(path, "FIRST=1\nFIRST=1\n")
    with pytest.raises(TargetError, match="more than once"):
        write_target(str(path), ["FIRST"], [(0, "2")])
    assert path.read_text() == "FIRST=1\nFIRST=1\n"


def test_the_new_content_is_complete_and_owner_only_before_it_takes_the_targets_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = put(tmp_path / ".env", "FIRST=old\n", mode=0o644)
    seen: list[tuple[str, int, str]] = []
    real_chmod = os.chmod

    def watching_chmod(temporary: str, mode: int) -> None:
        found = Path(temporary)
        seen.append((found.read_text(), stat.S_IMODE(found.stat().st_mode), path.read_text()))
        assert found.parent == path.parent and found != path
        real_chmod(temporary, mode)

    monkeypatch.setattr(os, "chmod", watching_chmod)
    assert update(path, FIRST="new") == "FIRST=new\n"
    assert seen == [("FIRST=new\n", 0o600, "FIRST=old\n")]
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert [entry.name for entry in tmp_path.iterdir()] == [".env"]


def test_a_failure_at_any_step_leaves_the_target_whole_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*arguments: object, **options: object) -> None:
        raise OSError("SENTINEL")

    path = put(tmp_path / ".env", "FIRST=1\n", mode=0o640)
    for step in ("fsync", "chmod", "replace"):
        with monkeypatch.context() as patch:
            patch.setattr(os, step, refuse)
            assert refusal(path, FIRST="2") == "the target file could not be written", step
    # When even the cleanup fails, the diagnostic says a file was left behind.
    monkeypatch.setattr(os, "replace", refuse)
    monkeypatch.setattr(os, "unlink", refuse)
    with pytest.raises(TargetError) as raised:
        update(path, FIRST="2")
    assert str(raised.value) == (
        "the target file could not be written, and a temporary file beside it could not be removed"
    )
    assert path.read_text() == "FIRST=1\n"


def test_the_owner_and_group_are_kept_or_the_update_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = put(tmp_path / ".env", "FIRST=1\n")
    owner = path.stat()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "chown", lambda found, uid, gid: calls.append((uid, gid)))
    assert update(path, FIRST="2") == "FIRST=2\n"
    assert calls == []  # the temporary file already had the target's owner and group

    real_stat = os.stat

    def other_owner(found: object, **options: object) -> os.stat_result:
        result = real_stat(found, **options)  # type: ignore[call-overload]
        if Path(str(found)).name.startswith("..env."):
            return os.stat_result((*result[:4], owner.st_uid + 1, owner.st_gid + 1, *result[6:]))
        return result

    monkeypatch.setattr(os, "stat", other_owner)
    assert update(path, FIRST="3") == "FIRST=3\n"
    assert calls == [(owner.st_uid, owner.st_gid)]

    def refuse(found: object, uid: int, gid: int) -> None:
        raise PermissionError("SENTINEL")

    monkeypatch.setattr(os, "chown", refuse)
    assert refusal(path, FIRST="4") == "the target file could not be written"
