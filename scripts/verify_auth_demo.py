"""Offline two-user HTTP verification with a real Spring server and a controlled Agent.

Run from the repository: python scripts/verify_auth_demo.py --build
No model provider is contacted. Credentials and reports stay in ignored .runtime files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from init_auth_demo import initialize

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "ollama_springboot" / "demo"


def request(base: str, path: str, method="GET", body=None, token=None, request_id=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if request_id:
        headers["X-Request-Id"] = request_id
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=20) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def frames(raw: str):
    for frame in raw.replace("\r", "").split("\n\n"):
        data = "\n".join(line[5:].strip() for line in frame.splitlines() if line.startswith("data:"))
        if data:
            yield json.loads(data)


def run(build: bool, serve_for: int, backend_port: int = 0, browser_test: bool = False, postgres_restart: bool = False):
    if postgres_restart and (browser_test or not os.getenv("CHAT_TEST_DB_URL", "").startswith("jdbc:postgresql:")):
        raise ValueError("--postgres-restart needs CHAT_TEST_DB_URL for a dedicated PostgreSQL test database")
    credentials = dict(line.split("=", 1) for line in initialize().read_text().splitlines() if "=" in line)
    if browser_test:
        # Fixed fixtures exist only in this temporary H2 server, never in a user's persisted database.
        credentials["DEMO_ALICE_PASSWORD"] = "alice-ui-test-password"
        credentials["DEMO_BOB_PASSWORD"] = "bob-ui-test-password"
    env = os.environ | credentials
    logs = ROOT / ".runtime" / "auth-demo-verification.log"
    if build:
        wrapper = BACKEND / ("mvnw.cmd" if os.name == "nt" else "mvnw")
        with logs.open("w", encoding="utf-8") as output:
            subprocess.run(
                [str(wrapper), "-q", "-DskipTests", "package"],
                cwd=BACKEND,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
            )
    jars = sorted((BACKEND / "target").glob("demo-*.jar"))
    if not jars:
        raise RuntimeError("Backend JAR missing. Run this script with --build.")
    java = shutil.which("java")
    if not java:
        raise RuntimeError("Java 21+ is required")
    # Oracle's Windows PATH launcher may spawn another JVM. Start the actual executable
    # so terminate()/wait() own the server rather than just its launcher process.
    java_info = subprocess.run(
        [java, "-XshowSettings:properties", "-version"], capture_output=True, text=True, check=True
    )
    home = re.search(r"^\s*java.home\s*=\s*(.+)$", java_info.stderr, re.MULTILINE)
    if home:
        actual = Path(home.group(1).strip()) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if actual.is_file():
            java = str(actual)
    upstream_calls = []
    routing_started, release_routing = threading.Event(), threading.Event()
    retry_question = "Choose a source editor for the retry check."

    class Agent(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.handle_api()

        def do_PUT(self):
            self.handle_api()

        def do_POST(self):
            self.handle_api()

        def handle_api(self):
            authorized = self.headers.get("X-API-Key") == credentials["AGENT_API_KEY"]
            upstream_calls.append({"path": self.path, "authorized": authorized})
            if not authorized:
                self.send_response(401)
                self.end_headers()
                return
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path.endswith("/routing/resolve"):
                if postgres_restart and data["query"] == retry_question:
                    routing_started.set()
                    release_routing.wait(timeout=5)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"schema_version": "intent-routing.v1", "status": "ready",
                                             "mode": "rag", "target": data["binding"]}).encode())
                return
            session = data.get("session_id", "")
            pending = "confirm" not in self.path and "needs confirmation" in data.get("query", "")
            self.send_response(200)
            if self.path.endswith("/stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                events = [{"type": "meta", "session_id": session}]
                if pending:
                    events.append({"type": "awaiting_confirmation", "sub_questions": ["Which database?"]})
                else:
                    events.extend(
                        [
                            {"type": "token", "delta": "Hello "},
                            {"type": "token", "delta": "world"},
                            {"type": "final", "status": "success", "final_answer": "Hello world"},
                        ]
                    )
                for event in events:
                    self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.02)
            else:
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "status": "success",
                            "session_id": session,
                            "final_answer": "Hello world",
                            "awaiting_human_confirmation": pending,
                            "pending_sub_questions": ["Which database?"] if pending else [],
                        }
                    ).encode()
                )

    agent = ThreadingHTTPServer(("127.0.0.1", 0), Agent)
    threading.Thread(target=agent.serve_forever, daemon=True).start()
    with socket.socket() as socket_probe:
        if backend_port and socket_probe.connect_ex(("127.0.0.1", backend_port)) == 0:
            raise RuntimeError(f"Port {backend_port} is already in use; choose another port")
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", backend_port))
        port = socket_probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    command = [
        java,
        "-jar",
        str(jars[0]),
        f"--server.port={port}",
        f"--app.fastapi.base-url=http://127.0.0.1:{agent.server_port}",
        "--app.cors.allowed-origins=http://localhost:3000,http://localhost:3007,http://127.0.0.1:3007",
    ]
    if postgres_restart:
        command.extend(["--spring.profiles.active=postgres", f"--spring.datasource.url={env['CHAT_TEST_DB_URL']}",
                        "--app.routing.mode=live", "--app.routing.traffic-percent=100"])
    else:
        command.append("--spring.datasource.url=jdbc:h2:mem:offline_auth_demo;DB_CLOSE_DELAY=-1")
    results = []

    def check(name, condition):
        results.append({"name": name, "passed": bool(condition)})
        print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)
        if not condition:
            raise AssertionError(name)

    def stop(process):
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def start(output):
        process = subprocess.Popen(
            command,
            cwd=BACKEND,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 70
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Spring exited; see {logs}")
                try:
                    if request(base, "/actuator/health")[0] == 200:
                        # Tomcat may accept requests before ApplicationRunner has seeded both users.
                        ready = all(
                            request(
                                base,
                                "/api/auth/login",
                                "POST",
                                {"username": name, "password": credentials[f"DEMO_{name.upper()}_PASSWORD"]},
                            )[0]
                            == 200
                            for name in ("alice", "bob")
                        )
                        if ready:
                            break
                except OSError:
                    pass
                time.sleep(0.25)
            else:
                raise TimeoutError(f"Spring startup timed out; see {logs}")
            return process
        except BaseException:
            stop(process)
            raise

    with logs.open("a", encoding="utf-8") as output:
        process = start(output)
        try:
            tokens = {}
            for username in ("alice", "bob"):
                status, raw = request(
                    base,
                    "/api/auth/login",
                    "POST",
                    {"username": username, "password": credentials[f"DEMO_{username.upper()}_PASSWORD"]},
                )
                check(f"{username} login", status == 200)
                tokens[username] = json.loads(raw)["access_token"]
            a, b = tokens["alice"], tokens["bob"]
            check("anonymous request rejected", request(base, "/api/conversations")[0] == 401)
            status, raw = request(base, "/api/v1/conversations", "POST", {}, a)
            check("owned conversation created", status == 201)
            conversation = json.loads(raw)["conversation_id"]
            request(base, f"/api/session-state/{conversation}", "PUT", {"facts": {"user_name": "Alice"}}, a)
            before = len(upstream_calls)
            check("Bob cannot read Alice facts", request(base, f"/api/session-state/{conversation}", token=b)[0] == 404)
            check(
                "Bob cannot modify Alice conversation",
                request(base, f"/api/conversations/{conversation}", "PATCH", {"title": "intruder"}, b)[0] == 404,
            )
            check(
                "Bob cannot invoke Alice Agent session",
                request(
                    base,
                    "/api/chat/stream",
                    "POST",
                    {"conversation_id": conversation, "messages": [{"role": "user", "content": "intruder"}]},
                    b,
                )[0]
                == 404,
            )
            check("denied requests never reach Agent", len(upstream_calls) == before)
            status, raw = request(
                base,
                "/api/chat/stream",
                "POST",
                {"conversation_id": conversation, "messages": [{"role": "user", "content": "needs confirmation"}]},
                a,
            )
            events = list(frames(raw))
            check(
                "owned stream pauses for confirmation",
                status == 200 and any(e["type"] == "awaiting_confirmation" for e in events),
            )
            task = events[0]["task_id"]
            before = len(upstream_calls)
            check(
                "Bob cannot resume Alice task",
                request(base, "/api/chat/confirm/stream", "POST", {"session_id": task}, b)[0] == 404,
            )
            check("denied resume never reaches Agent", len(upstream_calls) == before)
            status, raw = request(
                base,
                "/api/chat/confirm/stream",
                "POST",
                {"session_id": task, "conversation_id": conversation, "action": "edit", "sub_questions": ["Postgres?"]},
                a,
            )
            events = list(frames(raw))
            check(
                "authorized resume streams text and final",
                status == 200
                and "".join(e.get("delta", "") for e in events) == "Hello world"
                and any(e["type"] == "final" for e in events),
            )
            status, raw = request(base, f"/api/v1/tasks/{task}", token=a)
            check("task is persisted as DONE", status == 200 and json.loads(raw)["status"] == "DONE")
            check(
                "answer is persisted in owned history",
                "Hello world" in request(base, f"/api/session-state/{conversation}", token=a)[1],
            )
            check(
                "Bob's list excludes Alice conversation",
                conversation not in request(base, "/api/conversations", token=b)[1],
            )
            check(
                "all upstream calls carry service authentication",
                bool(upstream_calls) and all(call["authorized"] for call in upstream_calls),
            )
            if postgres_restart:
                body = {"routing_mode": "new", "messages": [{"role": "user", "content": retry_question}]}
                key = str(uuid.uuid4())
                before = len(upstream_calls)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(request, base, "/api/chat/stream", "POST", body, a, key)
                    try:
                        check("first request reaches live routing", routing_started.wait(timeout=5))
                        second = pool.submit(request, base, "/api/chat/stream", "POST", body, a, key)
                        # The concurrent clients share a key while the Agent has not yet selected a route.
                        # A different payload can be rejected before the waiting Agent is released.
                        conflict = body | {"messages": [{"role": "user", "content": "Different question"}]}
                        check("in-flight key rejects a different payload", request(base, "/api/chat/stream", "POST", conflict, a, key)[0] == 409)
                    finally:
                        release_routing.set()
                    replies = [first.result(), second.result()]
                tasks = [{e["task_id"] for e in frames(raw) if e.get("task_id")} for _, raw in replies]
                check("simultaneous sends return the same committed task",
                      all(status == 200 for status, _ in replies) and len(tasks[0]) == 1 and tasks[0] == tasks[1])
                paths = [c["path"] for c in upstream_calls[before:]]
                check("live routing and generation each run once",
                      paths.count("/api/v1/routing/resolve") == 1 and paths.count("/api/v1/recommend/stream") == 1)
                stop(process)
                process = start(output)
                status, raw = request(base, f"/api/v1/tasks/{task}", token=a)
                recovered = json.loads(raw)
                check("original task and conversation survive JVM replacement",
                      status == 200 and recovered.get("task_id") == task and recovered.get("conversation_id") == conversation
                      and recovered.get("status") == "DONE" and recovered.get("final_result") == "Hello world")
                status, raw = request(base, f"/api/session-state/{conversation}", token=a)
                check("original assistant message survives JVM replacement",
                      status == 200 and any(m.get("role") == "assistant" and m.get("content") == "Hello world"
                                            for m in json.loads(raw)["messages"]))
            report = {
                "mode": "offline HTTP Agent stub; no real model calls",
                "checks": results,
                "upstream_calls": len(upstream_calls),
            }
            report_path = ROOT / ".runtime" / "auth-demo-verification.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Report: {report_path}", flush=True)
            if serve_for:
                print(f"Demo backend: {base} (available for {serve_for} seconds)", flush=True)
                time.sleep(serve_for)
        finally:
            stop(process)
            agent.shutdown()
            agent.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--serve-for", type=int, default=0, help="Keep verified demo backend running for UI inspection")
    parser.add_argument("--port", type=int, default=0, help="Optional backend port for browser inspection")
    parser.add_argument(
        "--browser-test", action="store_true", help="Use fixed passwords in the temporary H2 test server only"
    )
    parser.add_argument("--postgres-restart", action="store_true",
                        help="Restart the Spring JVM and check its persisted data in CHAT_TEST_DB_URL (DB_USER and DB_PASSWORD)")
    args = parser.parse_args()
    run(args.build, max(0, args.serve_for), args.port, args.browser_test, args.postgres_restart)
