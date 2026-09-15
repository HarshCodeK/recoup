"""Pytest configuration.

Forces the LLM provider to StubProvider for all unit tests so the suite
runs offline, deterministically, and fast. Live LLM is exercised in the
explicit scripts/live_llm_smoke.py smoke test, gated on env presence.
"""
import os

# Wipe any inherited provider config so get_default_provider() returns StubProvider
for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL", "ANTHROPIC_API_KEY"):
    os.environ.pop(key, None)