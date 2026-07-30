"""Tests for the agent_tools package: registry dispatch, hooks, error handling."""

from __future__ import annotations

import unittest
from typing import Any

from software_recommend_system.agent_tools import (
    HookRunner,
    Tool,
    ToolRegistry,
    ToolResult,
    default_registry,
)
from software_recommend_system.document_schema import Document


class _EchoTool(Tool):
    name = "echo"
    description = "Returns the input value in meta."
    input_schema = {"type": "object", "properties": {"value": {"type": "string"}}}

    def execute(self, *, value: Any = None, **_: Any) -> ToolResult:
        return ToolResult(meta={"echoed": value})


class _RaisingTool(Tool):
    name = "boom"
    description = "Always raises."
    input_schema = {}

    def execute(self, **_: Any) -> ToolResult:
        raise RuntimeError("kaboom")


class _DocsTool(Tool):
    name = "docs"
    description = "Returns a fixed Document list."
    input_schema = {}

    def execute(self, **_: Any) -> ToolResult:
        return ToolResult(
            documents=[
                Document(
                    content="hello",
                    metadata={"source": "test", "doc_id": "d1"},
                    score=0.5,
                )
            ]
        )


class _BadReturnTool(Tool):
    name = "bad"
    description = "Returns a non-ToolResult value (programming error)."
    input_schema = {}

    def execute(self, **_: Any):  # type: ignore[override]
        return {"not": "a ToolResult"}


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()

    def test_register_get_list(self) -> None:
        self.registry.register(_EchoTool())
        self.assertEqual(self.registry.list(), ["echo"])
        self.assertIsInstance(self.registry.get("echo"), _EchoTool)

    def test_get_missing_tool_raises_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            self.registry.get("does_not_exist")

    def test_execute_returns_toolresult_with_trace_meta(self) -> None:
        self.registry.register(_EchoTool())
        result = self.registry.execute("echo", value="hi")
        self.assertTrue(result.ok)
        self.assertEqual(result.meta["echoed"], "hi")
        self.assertIn("trace_id", result.meta)
        self.assertIn("elapsed_ms", result.meta)

    def test_execute_converts_exception_to_error_result(self) -> None:
        self.registry.register(_RaisingTool())
        result = self.registry.execute("boom")
        self.assertFalse(result.ok)
        self.assertIn("kaboom", result.error or "")
        # Registry never raises — caller can treat as partial failure.

    def test_execute_passes_documents_through(self) -> None:
        self.registry.register(_DocsTool())
        result = self.registry.execute("docs")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(result.documents[0].content, "hello")

    def test_execute_validates_return_type(self) -> None:
        self.registry.register(_BadReturnTool())
        result = self.registry.execute("bad")
        self.assertFalse(result.ok)
        self.assertIn("expected ToolResult", result.error or "")


class HookRunnerTests(unittest.TestCase):
    def test_before_after_hooks_fire_in_registration_order(self) -> None:
        registry = ToolRegistry()
        registry.register(_EchoTool())
        fired = []
        registry.hooks.add_before(lambda t, i: fired.append(("before-1", t.name)))
        registry.hooks.add_before(lambda t, i: fired.append(("before-2", t.name)))
        registry.hooks.add_after(lambda t, i, r: fired.append(("after-1", r.ok)))

        registry.execute("echo", value="x")
        self.assertEqual(
            fired,
            [("before-1", "echo"), ("before-2", "echo"), ("after-1", True)],
        )

    def test_hook_exception_is_swallowed_and_does_not_break_call(self) -> None:
        registry = ToolRegistry()
        registry.register(_EchoTool())
        registry.hooks.add_before(lambda t, i: (_ for _ in ()).throw(ValueError("hook fail")))
        result = registry.execute("echo", value="ok")
        self.assertTrue(result.ok)  # Tool still ran despite hook error.

    def test_after_hook_runs_even_on_tool_error(self) -> None:
        registry = ToolRegistry()
        registry.register(_RaisingTool())
        seen = []
        registry.hooks.add_after(lambda t, i, r: seen.append(r.ok))
        registry.execute("boom")
        self.assertEqual(seen, [False])

    def test_hook_runner_counts_track_registrations(self) -> None:
        runner = HookRunner()
        self.assertEqual((runner.before_count, runner.after_count), (0, 0))
        runner.add_before(lambda t, i: None)
        runner.add_after(lambda t, i, r: None)
        runner.add_after(lambda t, i, r: None)
        self.assertEqual((runner.before_count, runner.after_count), (1, 2))


class DefaultRegistryTests(unittest.TestCase):
    def test_default_registry_has_retrieval_tools(self) -> None:
        registry = default_registry()
        self.assertEqual(
            registry.list(), ["image_vector", "keyword", "memory", "vector", "web"]
        )

    def test_default_registry_is_singleton(self) -> None:
        self.assertIs(default_registry(), default_registry())


if __name__ == "__main__":
    unittest.main()
