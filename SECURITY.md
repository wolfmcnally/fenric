# Security

Fenric shows content a program composed to a person and hands what they answered back to that program, sometimes including credentials. A flaw in how it contains that content or handles those answers matters, and reports are welcome.

## Reporting a problem

Report a vulnerability privately through GitHub: on the [repository's Security tab](https://github.com/wolfmcnally/fenric/security), choose **Report a vulnerability**. Please do not open a public issue for it.

A useful report says which version you used, on which platform, the spec or command that shows the problem (with made-up values), and what you observed. A fix is released with an entry in the [changelog](CHANGELOG.md).

## What is in scope

The [security model](docs/security.md) states what Fenric promises. Anything that breaks one of those promises is in scope, for example:

- a value typed into a `secret` dialog reaching Fenric's standard output, standard error, exit status, a log, or a fallback file;
- a file target written in a form its readers would load differently, or left half-written;
- `custom` HTML that runs script, loads anything from outside, or passes itself off as Fenric's own prompt;
- generated chart code that escapes its opaque frame into trusted chrome, native Python/filesystem APIs or the validated result boundary; ordinary chart networking is authorized;
- an answer Fenric returns that the spec did not ask for;
- a diagnostic that quotes a value from the spec or from the person;
- a window that outlives its time limit;
- a temporary remote surface accepting a foreign tailnet identity or origin, leaking an answer, writing a sink twice, or leaving its owned listener/proxy after ending.

Out of scope: the calling program, the store command it names, and anything that can already read the files it names — Fenric does not defend a user from their own processes. Chart networking is authorized for cooperative caller JavaScript; real Linux/Windows GUI remains unverified.

## Supported versions

Fenric is pre-1.0. Fixes go into the latest release only.
