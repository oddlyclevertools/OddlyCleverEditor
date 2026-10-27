"""Bring-your-own judge: an OpenAI-compatible server (faked here) ranks the candidates."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cliptool import judge_llm


class FakeLLM(BaseHTTPRequestHandler):
    reply = ""
    seen: dict = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.seen = {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
        out = json.dumps({"choices": [{"message": {"content": FakeLLM.reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture()
def llm():
    httpd = HTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()


def test_asks_chat_completions_and_parses_a_thinking_reply(llm, monkeypatch):
    monkeypatch.setenv(judge_llm.KEY_ENV, "sk-test")
    FakeLLM.reply = '<think>maybe [1] or [2]</think>Sure!\n```json\n[{"id": 4, "score": 8, "title": "Hook"}]\n```'
    assert judge_llm.ask("rank these", llm, "qwen3:8b") == [{"id": 4, "score": 8, "title": "Hook"}]
    assert FakeLLM.seen["path"] == "/v1/chat/completions"
    assert FakeLLM.seen["body"]["model"] == "qwen3:8b"
    assert FakeLLM.seen["body"]["messages"][0]["content"] == "rank these"
    assert FakeLLM.seen["auth"] == "Bearer sk-test"


def test_bad_urls_and_unreachable_servers_are_clear_errors():
    with pytest.raises(ValueError):
        judge_llm.check_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        judge_llm.ask("x", "http://127.0.0.1:9/v1", "  ")
    with pytest.raises(RuntimeError, match="Couldn't reach"):
        judge_llm.ask("x", "http://127.0.0.1:9/v1", "m", timeout=2)
    assert judge_llm.is_local("http://localhost:1234/v1") and not judge_llm.is_local("https://api.example.com/v1")


def test_a_non_json_answer_is_reported(llm):
    FakeLLM.reply = "I think clip 3 is best."
    with pytest.raises(ValueError, match="isn't valid JSON"):
        judge_llm.ask("x", llm, "m")


def test_key_is_not_sent_in_clear_to_another_host(monkeypatch):
    captured = {}

    class Opener:
        def open(self, req, timeout):
            captured["auth"] = req.get_header("Authorization")
            raise OSError("stop here")

    monkeypatch.setenv(judge_llm.KEY_ENV, "sk-test")
    monkeypatch.setattr(judge_llm, "_OPENER", Opener())
    with pytest.raises(RuntimeError):
        judge_llm.ask("x", "http://192.0.2.10:8000/v1", "m")
    assert captured["auth"] is None
    with pytest.raises(RuntimeError):
        judge_llm.ask("x", "https://api.example.com/v1", "m")
    assert captured["auth"] == "Bearer sk-test"


class Redirector(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))  # unread data would reset the socket on Windows
        self.send_response(307)
        self.send_header("Location", "http://198.51.100.1/steal")
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_redirects_are_not_followed():
    httpd = HTTPServer(("127.0.0.1", 0), Redirector)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError, match="redirected"):
            judge_llm.ask("x", f"http://127.0.0.1:{httpd.server_port}/v1", "m", timeout=5)
    finally:
        httpd.shutdown()
