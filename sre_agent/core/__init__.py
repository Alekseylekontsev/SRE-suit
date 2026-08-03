"""Deterministic control-plane core for the SRE Agent.

This package holds the safety path — models, operation classification, the
single-action executor, batch lifecycle, and audit. None of it depends on an
LLM: every privileged action is classified, gated, secret-leased, and audited
by pure-Python logic (design principle P1).
"""
