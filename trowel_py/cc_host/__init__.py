"""trowel_py.cc_host — Claude Code subprocess host (slice022 backend).

Manages a long-lived `claude -p --input-format stream-json` subprocess per
session, feeds user input, and translates CC's raw stream-json events into
trowel's own event model (the only contract the frontend consumes).
"""
