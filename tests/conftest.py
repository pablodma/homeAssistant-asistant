"""Test bootstrap helpers."""

import sys
import types


# Some local environments don't have anthropic installed; tests only need imports to succeed.
if "anthropic" not in sys.modules:
    anthropic_stub = types.ModuleType("anthropic")

    class AsyncAnthropic:  # pragma: no cover - import shim
        """Minimal shim for tests that import qa_reviewer transitively."""

        def __init__(self, *args, **kwargs):
            pass

    anthropic_stub.AsyncAnthropic = AsyncAnthropic
    sys.modules["anthropic"] = anthropic_stub
