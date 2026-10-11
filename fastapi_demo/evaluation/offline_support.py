"""Test-only adapters. Installed only by the offline evaluation bootstrap."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from types import SimpleNamespace
from unittest.mock import patch

_PATCHES: list = []


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host is None or host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def block_external_network() -> None:
    """Disallow remote DNS and connections while permitting local HTTP tests."""
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_hostname = socket.gethostbyname
    original_hostname_ex = socket.gethostbyname_ex

    def hostname(host):
        if not _is_loopback(host):
            raise RuntimeError("Offline tests prohibit external DNS/network access")
        return original_hostname(host)

    def hostname_ex(host):
        if not _is_loopback(host):
            raise RuntimeError("Offline tests prohibit external DNS/network access")
        return original_hostname_ex(host)

    def resolve(host, *args, **kwargs):
        if not _is_loopback(host):
            raise RuntimeError("Offline tests prohibit external DNS/network access")
        return original_resolve(host, *args, **kwargs)

    def connect(sock, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            raise RuntimeError("Offline tests prohibit external network access")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            raise RuntimeError("Offline tests prohibit external network access")
        return original_connect_ex(sock, address)

    def sendto(sock, *args):
        address = args[-1]
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            raise RuntimeError("Offline tests prohibit external network access")
        return original_sendto(sock, *args)

    for target, replacement in (
        ("socket.getaddrinfo", resolve),
        ("socket.socket.connect", connect),
        ("socket.socket.connect_ex", connect_ex),
        ("socket.socket.sendto", sendto),
        ("socket.gethostbyname", hostname),
        ("socket.gethostbyname_ex", hostname_ex),
    ):
        patcher = patch(target, replacement)
        patcher.start()
        _PATCHES.append(patcher)


def fake_embeddings(texts: list[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        vector = [0.0] * 32
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[digest[0] % len(vector)] += 1
        norm = sum(value * value for value in vector) ** 0.5
        vectors.append([value / norm for value in vector] if norm else [1.0] + [0.0] * 31)
    return vectors


class FakeOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.embeddings = SimpleNamespace(create=self.embed)

    def embed(self, *, input, **kwargs):
        texts = [input] if isinstance(input, str) else input
        return SimpleNamespace(data=[SimpleNamespace(embedding=vector) for vector in fake_embeddings(texts)])

    def create(self, *, messages, stream=False, **kwargs):
        system = " ".join(str(message.get("content", "")) for message in messages if message["role"] == "system")
        query = str(messages[-1].get("content", ""))
        if "__perf_fail__" in query:
            raise RuntimeError("Injected model failure")
        if "__perf_slow__" in query or "__perf_hold__" in query:
            from software_recommend_system.execution_control import check_current_run, current_control

            deadline = time.monotonic() + (12 if "__perf_slow__" in query else 3)
            while time.monotonic() < deadline:
                control = current_control()
                if control:
                    control.stop_event.wait(0.02)
                else:
                    time.sleep(0.02)
                check_current_run()
        if "normalized_queries" in system:
            content = json.dumps({"normalized_queries": [query]})
        elif "sub_questions" in system or "JSON array" in system and "sub-question" in system:
            content = json.dumps([query])
        elif "candidates" in system:
            content = json.dumps(
                {
                    "candidates": [
                        {
                            "solution": "Offline solution",
                            "rationale": "Fixed evidence",
                            "pros": [],
                            "cons": [],
                            "relevance_score": 0.9,
                        }
                    ]
                }
            )
        else:
            content = "Offline answer based on the supplied evidence."
        if stream:

            def chunks():
                for token in ("Offline answer ", "based on the supplied evidence."):
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=token))])

            return chunks()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def install_adapters() -> None:
    import openai

    from software_recommend_system import observability

    openai.OpenAI = FakeOpenAI
    observability._langsmith_wrap_openai = None
    block_external_network()
