import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from software_recommend_system import agent_loop as al
from software_recommend_system.agent_tools.base import Tool, ToolResult
from software_recommend_system.agent_tools.registry import ToolRegistry
from software_recommend_system.document_schema import Document, Metadata


def _doc(doc_id: str, content: str = "content") -> Document:
    return Document(content=content, metadata=Metadata(source="vector", doc_id=doc_id), score=0.9)


class _StubTool(Tool):
    name = "vector"
    description = "stub"
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    def __init__(self, docs=None, error=None):
        self.docs = docs if docs is not None else [_doc("d1")]
        self.error = error
        self.calls: list[dict] = []

    def execute(self, **inputs):
        self.calls.append(inputs)
        if self.error:
            return ToolResult(error=self.error)
        return ToolResult(documents=list(self.docs))


class _BoomTool(_StubTool):
    name = "keyword"

    def execute(self, **inputs):
        self.calls.append(inputs)
        raise RuntimeError("tool exploded")


def _tool_call(call_id: str, name: str, arguments) -> SimpleNamespace:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=raw),
    )


def _msg(content="", tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _resp(message) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class _ScriptedClient:
    """Replays a list of responses (or raises a queued exception)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.last_kwargs = None

        def create(**kwargs):
            self.calls += 1
            self.last_kwargs = kwargs
            item = self.script.pop(0) if self.script else _resp(_msg("done"))
            if isinstance(item, BaseException):
                raise item
            return item

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _registry(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool)
    return reg


class ToolSpecTests(unittest.TestCase):
    def test_specs_render_from_registry(self) -> None:
        specs = al.build_tool_specs(_registry(_StubTool()))
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["type"], "function")
        self.assertEqual(specs[0]["function"]["name"], "vector")
        self.assertIn("parameters", specs[0]["function"])


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(al.settings, "AGENT_LOOP_MODEL", "loop-model")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_executes_tool_call_and_collects_documents(self) -> None:
        tool = _StubTool(docs=[_doc("d1"), _doc("d2")])
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "vector", {"query": "kafka"})])),
            _resp(_msg("done")),
        ])
        docs = al.run_agent_loop("kafka", registry=_registry(tool), client=client)

        self.assertIsNotNone(docs)
        self.assertEqual([d.metadata.doc_id for d in docs], ["d1", "d2"])
        self.assertEqual(tool.calls[0]["query"], "kafka")

    def test_tools_are_advertised_to_the_model(self) -> None:
        client = _ScriptedClient([_resp(_msg("done"))])
        al.run_agent_loop("q", registry=_registry(_StubTool()), client=client)
        self.assertEqual(client.last_kwargs["tool_choice"], "auto")
        self.assertEqual(client.last_kwargs["tools"][0]["function"]["name"], "vector")

    def test_duplicate_call_is_refused_not_executed(self) -> None:
        """A model looping on identical arguments is the classic failure mode."""
        tool = _StubTool()
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "vector", {"query": "kafka"})])),
            _resp(_msg(tool_calls=[_tool_call("c2", "vector", {"query": "kafka"})])),
            _resp(_msg("done")),
        ])
        al.run_agent_loop("kafka", registry=_registry(tool), client=client)
        self.assertEqual(len(tool.calls), 1)

    def test_unknown_tool_name_does_not_crash(self) -> None:
        """ToolRegistry.execute() raises KeyError for unregistered names and an
        LLM will eventually hallucinate one."""
        tool = _StubTool()
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "nonexistent", {"query": "x"})])),
            _resp(_msg("done")),
        ])
        docs = al.run_agent_loop("q", registry=_registry(tool), client=client)
        self.assertIsNone(docs)
        self.assertEqual(len(tool.calls), 0)

    def test_malformed_arguments_do_not_crash(self) -> None:
        tool = _StubTool()
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "vector", "{not json")])),
            _resp(_msg("done")),
        ])
        al.run_agent_loop("q", registry=_registry(tool), client=client)
        self.assertEqual(len(tool.calls), 0)

    def test_tool_error_result_is_reported_not_raised(self) -> None:
        tool = _StubTool(error="chroma down")
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "vector", {"query": "x"})])),
            _resp(_msg("done")),
        ])
        self.assertIsNone(al.run_agent_loop("q", registry=_registry(tool), client=client))

    def test_raising_tool_is_contained_by_registry(self) -> None:
        boom = _BoomTool()
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "keyword", {"query": "x"})])),
            _resp(_msg("done")),
        ])
        self.assertIsNone(al.run_agent_loop("q", registry=_registry(boom), client=client))

    def test_llm_failure_degrades_to_none(self) -> None:
        client = _ScriptedClient([RuntimeError("upstream down")])
        self.assertIsNone(al.run_agent_loop("q", registry=_registry(_StubTool()), client=client))

    def test_step_budget_is_enforced(self) -> None:
        tool = _StubTool()
        script = [
            _resp(_msg(tool_calls=[_tool_call(f"c{i}", "vector", {"query": f"q{i}"})]))
            for i in range(10)
        ]
        client = _ScriptedClient(script)
        with patch.object(al.settings, "AGENT_LOOP_MAX_STEPS", 2):
            al.run_agent_loop("q", registry=_registry(tool), client=client)
        self.assertEqual(client.calls, 2)

    def test_tool_call_budget_is_enforced(self) -> None:
        tool = _StubTool()
        script = [
            _resp(_msg(tool_calls=[_tool_call(f"c{i}", "vector", {"query": f"q{i}"})]))
            for i in range(10)
        ]
        client = _ScriptedClient(script)
        with (
            patch.object(al.settings, "AGENT_LOOP_MAX_STEPS", 10),
            patch.object(al.settings, "AGENT_LOOP_MAX_TOOL_CALLS", 2),
        ):
            al.run_agent_loop("q", registry=_registry(tool), client=client)
        self.assertLessEqual(len(tool.calls), 2)

    def test_no_model_configured_returns_none_without_calling(self) -> None:
        client = _ScriptedClient([_resp(_msg("done"))])
        with (
            patch.object(al.settings, "AGENT_LOOP_MODEL", ""),
            patch.object(al.settings, "LLM_MODEL", ""),
        ):
            self.assertIsNone(al.run_agent_loop("q", registry=_registry(_StubTool()), client=client))
        self.assertEqual(client.calls, 0)

    def test_empty_question_returns_none(self) -> None:
        client = _ScriptedClient([_resp(_msg("done"))])
        self.assertIsNone(al.run_agent_loop("   ", registry=_registry(_StubTool()), client=client))
        self.assertEqual(client.calls, 0)

    def test_session_scoped_inputs_are_injected_not_model_supplied(self) -> None:
        class _MemTool(_StubTool):
            name = "memory"

        tool = _MemTool()
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "memory", {"query": "x", "session_id": "spoofed"})])),
            _resp(_msg("done")),
        ])
        al.run_agent_loop(
            "q", registry=_registry(tool), client=client,
            session_id="real-session", memory_context=[{"content": "m"}],
        )
        self.assertEqual(tool.calls[0]["session_id"], "real-session")

    def test_documents_are_deduplicated_across_calls(self) -> None:
        tool = _StubTool(docs=[_doc("same")])
        client = _ScriptedClient([
            _resp(_msg(tool_calls=[_tool_call("c1", "vector", {"query": "a"})])),
            _resp(_msg(tool_calls=[_tool_call("c2", "vector", {"query": "b"})])),
            _resp(_msg("done")),
        ])
        docs = al.run_agent_loop("q", registry=_registry(tool), client=client)
        self.assertEqual(len(docs), 1)


class RetrieveNodeIntegrationTests(unittest.TestCase):
    """The flag must actually change retrieve_node behavior, and a loop that
    returns nothing must fall back to the static pipeline."""

    def setUp(self) -> None:
        # retrieve_node calls rerank_documents, which would otherwise try to
        # download/load the cross-encoder. Keep these tests hermetic and fast.
        from software_recommend_system import retriever

        patcher = patch.object(retriever.settings, "RERANK_MODEL_ENABLED", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self):
        from software_recommend_system.state import AgentState

        return AgentState(user_query="kafka", sub_questions=["kafka partitions"])

    def test_disabled_by_default(self) -> None:
        from software_recommend_system import nodes

        self.assertFalse(nodes.settings.AGENT_LOOP_ENABLE)

    def test_flag_off_uses_static_pipeline_only(self) -> None:
        from software_recommend_system import nodes

        with (
            patch.object(nodes.settings, "AGENT_LOOP_ENABLE", False),
            patch.object(nodes, "run_agent_loop") as loop,
            patch.object(nodes, "retrieve_with_search") as static,
        ):
            static.return_value = SimpleNamespace(result=lambda: [_doc("static-1")])
            result = nodes.retrieve_node(self._state())

        loop.assert_not_called()
        static.assert_called_once()
        self.assertEqual(result["retrieval_records"][0]["retrieval_strategy"], "static")

    def test_flag_on_uses_agent_loop_results(self) -> None:
        from software_recommend_system import nodes

        with (
            patch.object(nodes.settings, "AGENT_LOOP_ENABLE", True),
            patch.object(nodes, "run_agent_loop", return_value=[_doc("agent-1")]) as loop,
            patch.object(nodes, "retrieve_with_search") as static,
        ):
            result = nodes.retrieve_node(self._state())

        loop.assert_called_once()
        static.assert_not_called()
        self.assertEqual(result["retrieval_records"][0]["retrieval_strategy"], "agent_loop")

    def test_agent_loop_failure_falls_back_to_static(self) -> None:
        from software_recommend_system import nodes

        with (
            patch.object(nodes.settings, "AGENT_LOOP_ENABLE", True),
            patch.object(nodes, "run_agent_loop", return_value=None) as loop,
            patch.object(nodes, "retrieve_with_search") as static,
        ):
            static.return_value = SimpleNamespace(result=lambda: [_doc("static-1")])
            result = nodes.retrieve_node(self._state())

        loop.assert_called_once()
        static.assert_called_once()
        self.assertEqual(result["retrieval_records"][0]["retrieval_strategy"], "static")


if __name__ == "__main__":
    unittest.main()
