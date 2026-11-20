# Fenric

A terminal prompt is invisible when nobody is watching the terminal, and an agent's chat is the wrong place for passwords or API keys. Fenric is the rung in between: a real window, on the screen now, that blocks the caller for a bounded time and comes back with an answer whose shape the caller can trust.

A caller sends one spec (a verb and its parameters), the person at the machine answers one window, and the caller reads one result: validated JSON or a plain status. Fenric decides how the question is shown and contained, so an agent or script can ask a person without building its own interface.

Read [Find the Documentation](documentation.md) for where each part of Fenric is described.

Fenric is developed with the [agentic coding starter](https://github.com/wolfmcnally/agentic-coding-starter) methodology.
