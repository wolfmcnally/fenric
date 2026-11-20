---
title: "Fenric — Product Brief"
date: 2026-11-20
status: draft
scope: Entry-point brief for Fenric. States the problem it solves, what it promises, and how its acceptance is judged.
---

# Fenric — Product Brief

*Native dialogs for agents and scripts: ask a person something, get a structured answer back.*

## Thesis

A terminal prompt is invisible when nobody is watching the terminal, and an agent's chat is the wrong place for passwords or API keys. Fenric is the rung in between: a real window, on the screen now, that blocks the caller for a bounded time and comes back with an answer whose shape the caller can trust. The caller says *what* to ask; Fenric decides how it is shown and contained.

## Catalog

See [`AGENTS.md` — Project briefs](../AGENTS.md#project-briefs) and [`AGENTS.md` — Methodology briefs](../AGENTS.md#methodology-briefs) for the index of sibling briefs.

## What Fenric provides

- One spec in, one result out: a verb and its parameters produce validated JSON or a plain status. The verbs, options and fields are specified in [the command reference](../docs/commands.md) and [the spec](../docs/spec.md).
- Contained content: what a program composes is shown to a person without letting it script the window, reach outside, or impersonate Fenric's own prompt. The promises are stated in [the security model](../docs/security.md).
- Bounded waiting: every dialog has a timeout, and cancellation, timeout and answer are distinct outcomes.

## Acceptance

- The test suite and documentation captures pass on the exact tree; every documented command reruns and prints what the documentation shows.
- The built wheel and source archive contain exactly the members listed in `package-inventory.txt`.
- Security-model promises keep direct proofs; a change that weakens one is a defect regardless of other results.
- Visual behaviour, product taste and acceptance on real devices are judged by a person and stay open until they are.
