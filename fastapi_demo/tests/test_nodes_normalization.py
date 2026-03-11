import unittest
from unittest.mock import patch

from software_recommend_system.config import settings
from software_recommend_system.nodes import query_normalization_node
from software_recommend_system.state import AgentState


class QueryNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._use_llm = settings.QUERY_NORMALIZATION_USE_LLM
        self._max_chars = settings.QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM

    def tearDown(self) -> None:
        settings.QUERY_NORMALIZATION_USE_LLM = self._use_llm
        settings.QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM = self._max_chars

    def test_skip_llm_normalization_when_disabled(self) -> None:
        settings.QUERY_NORMALIZATION_USE_LLM = False
        state = AgentState(user_query="How to transfer SPSS license to new computer?")

        with patch("software_recommend_system.nodes.normalize_query_with_llm") as mocked_llm:
            result = query_normalization_node(state)

        mocked_llm.assert_not_called()
        self.assertEqual(result["normalized_query"], state.user_query)


if __name__ == "__main__":
    unittest.main()

