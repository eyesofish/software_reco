"""Tests for app.api.v1.auth: dev-mode passthrough, header validation, admin path."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.v1 import auth


def _run(coro):
    return asyncio.run(coro)


class RequireApiKeyTests(unittest.TestCase):
    def test_dev_mode_no_key_configured_passes_through(self) -> None:
        with patch.object(auth.settings, "API_KEY", ""):
            _run(auth.require_api_key(x_api_key=None))  # no exception

    def test_correct_header_passes(self) -> None:
        with patch.object(auth.settings, "API_KEY", "secret-key-123"):
            _run(auth.require_api_key(x_api_key="secret-key-123"))

    def test_missing_header_when_configured_raises_401(self) -> None:
        with patch.object(auth.settings, "API_KEY", "secret-key-123"):
            with self.assertRaises(HTTPException) as ctx:
                _run(auth.require_api_key(x_api_key=None))
            self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_header_raises_401(self) -> None:
        with patch.object(auth.settings, "API_KEY", "secret-key-123"):
            with self.assertRaises(HTTPException) as ctx:
                _run(auth.require_api_key(x_api_key="wrong"))
            self.assertEqual(ctx.exception.status_code, 401)


class RequireAdminApiKeyTests(unittest.TestCase):
    def test_dev_mode_passes_through(self) -> None:
        with patch.object(auth.settings, "ADMIN_API_KEY", ""), patch.object(
            auth.settings, "API_KEY", ""
        ):
            _run(auth.require_admin_api_key(x_api_key=None))

    def test_falls_back_to_api_key_when_admin_unset(self) -> None:
        with patch.object(auth.settings, "ADMIN_API_KEY", ""), patch.object(
            auth.settings, "API_KEY", "user-key"
        ):
            _run(auth.require_admin_api_key(x_api_key="user-key"))
            with self.assertRaises(HTTPException):
                _run(auth.require_admin_api_key(x_api_key="wrong"))

    def test_admin_key_required_when_configured(self) -> None:
        with patch.object(auth.settings, "ADMIN_API_KEY", "admin-secret"), patch.object(
            auth.settings, "API_KEY", "user-key"
        ):
            _run(auth.require_admin_api_key(x_api_key="admin-secret"))
            with self.assertRaises(HTTPException):
                _run(auth.require_admin_api_key(x_api_key="user-key"))


class NormalizersImportTests(unittest.TestCase):
    """Smoke-test that the refactored handler modules import and the
    moved helpers behave the same as the pre-refactor versions."""

    def test_extract_name_fact_zh_and_en(self) -> None:
        from app.api.v1.handlers.normalizers import _extract_name_fact

        self.assertEqual(_extract_name_fact("我叫张三"), "张三")
        self.assertEqual(_extract_name_fact("My name is Alice"), "Alice")
        self.assertIsNone(_extract_name_fact(""))
        self.assertIsNone(_extract_name_fact("hello"))

    def test_is_asking_user_name(self) -> None:
        from app.api.v1.handlers.normalizers import _is_asking_user_name

        self.assertTrue(_is_asking_user_name("what is my name?"))
        self.assertTrue(_is_asking_user_name("我是谁"))
        self.assertFalse(_is_asking_user_name("recommend a database"))

    def test_normalize_pending_sub_questions_handles_json_string(self) -> None:
        from app.api.v1.handlers.normalizers import _normalize_pending_sub_questions

        self.assertEqual(
            _normalize_pending_sub_questions('["q1", "q2"]'),
            ["q1", "q2"],
        )
        self.assertEqual(
            _normalize_pending_sub_questions(["a", "a", "b", ""]),
            ["a", "b"],
        )


if __name__ == "__main__":
    unittest.main()
