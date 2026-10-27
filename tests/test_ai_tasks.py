"""AI jobs beyond judging: hook titles for ticked clips and transcript fixes, plus their UI routes."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from cliptool import ai_tasks
from cliptool import transcript as tr
from cliptool.ui import actions
from cliptool.ui.jobs import Cancelled

from test_ui import call, server, video  # noqa: F401 - shared fixtures

TEXT = "Callan walks into Londinium. He meets Boudica. Callan runs for 200 metres."
WORDS = tuple(tr.Word(round(0.4 * i, 2), round(0.4 * i + 0.3, 2), t) for i, t in enumerate(TEXT.split()))


def test_clip_texts_and_titles_prompt():
    clips = ai_tasks.clip_texts(WORDS, [{"id": 3, "start": 0, "end": 1.55}, {"id": 4, "start": 2.0, "end": 3.0}])
    assert clips[0].text == "Callan walks into Londinium." and clips[1].id == 4
    prompt = ai_tasks.titles_prompt("ep.mp4", clips)
    assert "### Clip 3" in prompt and "Callan walks into Londinium." in prompt and '"title"' in prompt
    with pytest.raises(ValueError):
        ai_tasks.clip_texts(WORDS, [])
    with pytest.raises(ValueError):
        ai_tasks.clip_texts(WORDS, [{"id": "x", "start": 0, "end": 1}])


def test_parse_titles_keeps_only_ticked_ids_and_trims():
    out = ai_tasks.parse_titles([{"id": 3, "title": '  "He  ran"  '}, {"id": 9, "title": "Not ticked"},
                                 {"id": True, "title": "bool id"}, {"id": 3, "title": "Duplicate"},
                                 {"id": 4, "title": "x" * 200}], {3, 4})
    assert out == [{"id": 3, "title": "He ran"}, {"id": 4, "title": "x" * ai_tasks.MAX_TITLE}]
    with pytest.raises(ValueError):
        ai_tasks.parse_titles([{"id": 9, "title": "nope"}], {3})


def test_fixes_prompt_and_parse_only_real_single_words():
    prompt = ai_tasks.fixes_prompt("ep.mp4", WORDS)
    assert "[0:00] Callan walks into Londinium." in prompt and "find" in prompt
    out = ai_tasks.parse_fixes([
        {"find": "Callan", "replace": "Callum", "why": "host name"},
        {"find": "Boudica", "replace": "Boudicca"},
        {"find": "Nowhere", "replace": "x"},              # not in the transcript
        {"find": "two words", "replace": "x"},            # not one word
        {"find": "Callan", "replace": "Calum"},           # duplicate
        {"find": "metres", "replace": "metres"},          # no change
        {"find": "walks", "replace": "a=b"},              # would break OLD=NEW
    ], WORDS)
    assert [(f["find"], f["replace"], f["count"]) for f in out] == [("Callan", "Callum", 2), ("Boudica", "Boudicca", 1)]
    assert ai_tasks.parse_fixes([], WORDS) == []
    with pytest.raises(ValueError):
        ai_tasks.parse_fixes({"find": "x"}, WORDS)


class FakeLLM(BaseHTTPRequestHandler):
    replies: list = []
    auth = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        FakeLLM.auth = self.headers.get("Authorization")
        out = json.dumps({"choices": [{"message": {"content": FakeLLM.replies.pop(0)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _project_with_transcript(app, video_path):  # noqa: ANN001
    pid = app.store.from_path(str(video_path)).id
    actions.save_transcript(app.store, app.store.get(pid), {
        "format": 1, "language": "en", "duration": 30.0, "model": "t",
        "segments": [{"start": 0, "end": 6, "text": TEXT,
                      "words": [{"start": w.start, "end": w.end, "text": w.text, "prob": 1} for w in WORDS]}]})
    return pid


def test_ai_routes_prompt_apply_file_and_ask(server, video, tmp_path: Path):  # noqa: F811
    app, port = server
    pid = _project_with_transcript(app, video)
    clips = [{"id": 1, "start": 0, "end": 1.55}]

    status, _, body = call(port, "POST", f"/api/projects/{pid}/ai/titles/prompt", {"clips": clips}, token=app.token)
    reply = json.loads(body)
    assert status == 200 and Path(reply["prompt_path"]).name == "ai_titles.md"
    assert Path(reply["prompt_path"]).read_text(encoding="utf-8") == reply["prompt"]

    status, _, body = call(port, "POST", f"/api/projects/{pid}/ai/titles/apply",
                           {"text": '```json\n[{"id": 1, "title": "Into Londinium"}]\n```', "clips": clips},
                           token=app.token)
    assert status == 200 and json.loads(body) == [{"id": 1, "title": "Into Londinium"}]

    status, _, body = call(port, "POST", f"/api/projects/{pid}/ai/fixes/apply", {"from_file": True}, token=app.token)
    assert status == 404
    Path(app.store.folder(pid) / "ai_fixes.json").write_text('[{"find": "Callan", "replace": "Callum"}]', encoding="utf-8")
    status, _, body = call(port, "POST", f"/api/projects/{pid}/ai/fixes/apply", {"from_file": True}, token=app.token)
    assert status == 200 and json.loads(body)[0]["count"] == 2

    assert call(port, "POST", f"/api/projects/{pid}/ai/explode/prompt", {}, token=app.token)[0] == 400

    llm = HTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    try:
        FakeLLM.replies = ['<think>hmm</think>[{"find": "Boudica", "replace": "Boudicca", "why": "spelling"}]']
        work = actions.job_ai(app.store, app.store.get(pid), "fixes", f"http://127.0.0.1:{llm.server_port}/v1",
                              "m", "sk-typed", 5, None)
        fixes = work(lambda f, m="": None)
        assert fixes[0]["replace"] == "Boudicca" and FakeLLM.auth == "Bearer sk-typed"
    finally:
        llm.shutdown()
    folder_text = " ".join(f.read_text(encoding="utf-8", errors="ignore") for f in app.store.folder(pid).glob("*")
                           if f.is_file() and f.suffix in (".json", ".md"))
    assert "sk-typed" not in folder_text  # the key typed in the UI is never written to disk


class SlowLLM(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        time.sleep(3)
        try:
            self.send_response(200)
            self.end_headers()
        except OSError:
            pass


def test_ai_job_can_be_cancelled_while_the_ai_thinks(server, video):  # noqa: F811
    app, _ = server
    pid = _project_with_transcript(app, video)
    llm = HTTPServer(("127.0.0.1", 0), SlowLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    calls = []

    def progress(fraction, message=""):
        calls.append(fraction)
        if len(calls) > 2:
            raise Cancelled("Cancelled.")
    try:
        work = actions.job_ai(app.store, app.store.get(pid), "fixes", f"http://127.0.0.1:{llm.server_port}/v1",
                              "m", None, 5, None)
        started = time.monotonic()
        with pytest.raises(Cancelled):
            work(progress)
        assert time.monotonic() - started < 2.5  # didn't wait for the slow reply
    finally:
        llm.shutdown()
