"""Minimal litellm stub — allows the app import chain to complete.
Only provides the bare minimum symbols needed by src/analyzer.py."""
Router = object
def completion(*a, **kw): return {"choices": [{"message": {"content": ""}}]}
