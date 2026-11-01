#!/usr/bin/env python3
"""Provider selection and request shapes of chia_svapshot.llm (no network)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chia_svapshot import llm  # noqa: E402


class ResolveTest(unittest.TestCase):
    def test_explicit_provider_prefix(self):
        for spec, provider, model in (
            ("vertex:gemini-2.5-pro", "vertex", "gemini-2.5-pro"),
            ("openai:gpt-5", "openai", "gpt-5"),
            ("nvidia:meta/llama-3.3-70b-instruct", "nvidia", "meta/llama-3.3-70b-instruct"),
            ("anthropic:claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5"),
            ("gemini:gemini-2.5-flash", "gemini", "gemini-2.5-flash"),
            ("mock:replay", "mock", "replay"),
        ):
            got = llm.resolve(spec)
            self.assertEqual((got.provider, got.model), (provider, model))

    def test_bare_names_pick_a_provider(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "p"}, clear=True):
            self.assertEqual(llm.resolve("gemini-2.5-pro").provider, "vertex")
            self.assertEqual(llm.resolve("claude-sonnet-4-5").provider, "anthropic")
            self.assertEqual(llm.resolve("gpt-5").provider, "openai")
            self.assertEqual(llm.resolve("o3-mini").provider, "openai")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=True):
            self.assertEqual(llm.resolve("gemini-2.5-pro").provider, "gemini")
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "k"}, clear=True):
            self.assertEqual(llm.resolve("meta/llama-3.3-70b-instruct").provider, "nvidia")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm.resolve("meta/llama-3.3-70b-instruct").provider, "openai")
            self.assertEqual(llm.resolve("qwen3-coder").provider, "openai")

    def test_model_name_keeps_colons_after_the_provider(self):
        got = llm.resolve("openai:qwen3:30b")
        self.assertEqual((got.provider, got.model), ("openai", "qwen3:30b"))

    def test_unknown_prefix_is_part_of_the_model(self):
        got = llm.resolve("llama3:8b")
        self.assertEqual((got.provider, got.model), ("openai", "llama3:8b"))

    def test_empty_spec_is_rejected(self):
        with self.assertRaises(llm.LLMError):
            llm.resolve("")
        with self.assertRaises(llm.LLMError):
            llm.resolve("openai:")

    def test_bare_model_strips_the_provider(self):
        self.assertEqual(llm.bare_model("vertex:gemini-2.5-pro"), "gemini-2.5-pro")
        self.assertEqual(llm.bare_model("gemini-2.5-pro"), "gemini-2.5-pro")


class RequestShapeTest(unittest.TestCase):
    def _capture(self, spec, reply, env):
        calls = []

        def fake_post(url, headers, body, **kwargs):
            calls.append((url, headers, body))
            return reply

        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(llm, "_post_json", fake_post):
            text = llm.generate(spec, "hello", max_output_tokens=1000)
        return text, calls[0]

    def test_openai_chat_completions(self):
        text, (url, headers, body) = self._capture(
            "openai:gpt-4.1",
            {"choices": [{"message": {"content": "module m; endmodule"}}]},
            {"OPENAI_API_KEY": "sk-test"},
        )
        self.assertEqual(text, "module m; endmodule")
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(body["max_tokens"], 1000)
        self.assertIn("temperature", body)

    def test_openai_reasoning_model_uses_completion_tokens(self):
        _, (_, _, body) = self._capture(
            "openai:gpt-5",
            {"choices": [{"message": {"content": "x"}}]},
            {"OPENAI_API_KEY": "sk-test"},
        )
        self.assertEqual(body["max_completion_tokens"], 1000)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)

    def test_openai_compatible_server_needs_no_key(self):
        _, (url, headers, body) = self._capture(
            "openai:qwen3-coder",
            {"choices": [{"message": {"content": "x"}}]},
            {"OPENAI_BASE_URL": "http://localhost:8000/v1/"},
        )
        self.assertEqual(url, "http://localhost:8000/v1/chat/completions")
        self.assertEqual(body["max_tokens"], 1000)

    def test_openai_without_key_or_server_reports_the_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate("openai:gpt-5", "hi")
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_nvidia_endpoint(self):
        _, (url, headers, _) = self._capture(
            "nvidia:meta/llama-3.3-70b-instruct",
            {"choices": [{"message": {"content": "x"}}]},
            {"NVIDIA_API_KEY": "nv-test"},
        )
        self.assertEqual(url, "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer nv-test")

    def test_anthropic_messages(self):
        text, (url, headers, body) = self._capture(
            "anthropic:claude-sonnet-4-5",
            {"content": [{"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}]},
            {"ANTHROPIC_API_KEY": "an-test"},
        )
        self.assertEqual(text, "abcd")
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "an-test")
        self.assertEqual(body["max_tokens"], 1000)

    def test_gemini_api_key(self):
        text, (url, headers, body) = self._capture(
            "gemini:gemini-2.5-flash",
            {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]},
            {"GEMINI_API_KEY": "g-test"},
        )
        self.assertEqual(text, "hi")
        self.assertTrue(url.endswith("/models/gemini-2.5-flash:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], "g-test")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 1000)

    def test_max_tokens_cap_from_environment(self):
        _, (_, _, body) = self._capture(
            "openai:gpt-4.1",
            {"choices": [{"message": {"content": "x"}}]},
            {"OPENAI_API_KEY": "k", "CHIA_LLM_MAX_TOKENS": "256"},
        )
        self.assertEqual(body["max_tokens"], 256)

    def test_vertex_needs_a_project(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate("vertex:gemini-2.5-pro", "hi")
        self.assertIn("GOOGLE_CLOUD_PROJECT", str(ctx.exception))

    def test_mock_provider_replays_a_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sv", delete=False) as handle:
            handle.write("module m; endmodule\n")
        try:
            with mock.patch.dict(os.environ, {"CHIA_LLM_MOCK_FILE": handle.name}, clear=True):
                self.assertEqual(llm.generate("mock:replay", "prompt"), "module m; endmodule\n")
        finally:
            os.unlink(handle.name)


class RewriteNodeTest(unittest.TestCase):
    def test_provider_failure_becomes_a_failed_attempt(self):
        from chia_svapshot import ppa_nodes
        from chia_svapshot.ppa_state import PpaDesign

        with tempfile.TemporaryDirectory() as tmp:
            rtl = os.path.join(tmp, "m.sv")
            with open(rtl, "w") as handle:
                handle.write("module m(input logic a, output logic y); assign y = a; endmodule\n")
            design = PpaDesign(
                name="m", module="m", module_type="combinational", workspace=tmp, tree=tmp,
                rtl_path=rtl, sources=[rtl],
            )
            fn = getattr(ppa_nodes.llm_rtl_rewrite, "__wrapped__", None) or ppa_nodes.llm_rtl_rewrite
            with mock.patch.dict(os.environ, {}, clear=True):
                result = fn(design, 0, "", "openai:gpt-5", "", "", "optimise")
        self.assertFalse(result.ok)
        self.assertIn("OPENAI_API_KEY", result.reason)


if __name__ == "__main__":
    unittest.main()
