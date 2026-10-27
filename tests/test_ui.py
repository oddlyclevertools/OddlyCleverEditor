"""Local UI server: security checks, video byte ranges, transcript editing, upload."""
import http.client
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cliptool.ui import actions
from cliptool.ui.server import App, make_handler

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("src") / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=3",
                    "-f", "lavfi", "-i", "sine=d=3", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
                   check=True)
    return path


@pytest.fixture()
def server(tmp_path: Path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    app = App(tmp_path / "ws", port)
    httpd.RequestHandlerClass = make_handler(app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield app, port
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, token=None, host=None, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    if token:
        h["X-Cliptool-Token"] = token
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    res = conn.getresponse()
    payload = res.read()
    conn.close()
    return res.status, dict(res.getheaders()), payload


def words():
    return [{"start": 0.2, "end": 0.6, "text": "Hello", "prob": 0.9},
            {"start": 0.6, "end": 1.2, "text": "Callan.", "prob": 0.4}]


def seed_transcript(app, pid):
    t = {"format": 1, "language": "en", "duration": 3.0, "model": "test",
         "segments": [{"start": 0.2, "end": 1.2, "text": "Hello Callan.", "words": words()}]}
    return actions.save_transcript(app.store, app.store.get(pid), t)


def test_index_carries_token_and_csp(server):
    app, port = server
    status, headers, body = call(port, "GET", "/")
    assert status == 200 and app.token.encode() in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]


def test_api_needs_token_localhost_host_and_same_origin(server):
    app, port = server
    assert call(port, "GET", "/api/state")[0] == 403
    assert call(port, "GET", "/api/state", token="wrong")[0] == 403
    assert call(port, "GET", "/api/state", token=app.token, host="evil.example:80")[0] == 403
    assert call(port, "POST", "/api/projects", {"path": "x"}, token=app.token,
                headers={"Origin": "http://evil.example"})[0] == 403
    status, _, body = call(port, "GET", "/api/state", token=app.token)
    assert status == 200 and "bold" in json.loads(body)["styles"]


def test_open_path_and_range_requests(server, video):
    app, port = server
    status, _, body = call(port, "POST", "/api/projects", {"path": str(video)}, token=app.token)
    assert status == 200
    pid = json.loads(body)["id"]
    size = video.stat().st_size
    status, headers, chunk = call(port, "GET", f"/media/{pid}/source?t={app.token}", headers={"Range": "bytes=10-109"})
    assert status == 206 and len(chunk) == 100 and headers["Content-Range"] == f"bytes 10-109/{size}"
    assert chunk == video.read_bytes()[10:110]
    status, _, _ = call(port, "GET", f"/media/{pid}/source?t={app.token}", headers={"Range": f"bytes={size + 5}-"})
    assert status == 416
    assert call(port, "GET", f"/media/{pid}/source")[0] == 403  # no token


def test_open_path_rejects_missing_and_non_video(server, tmp_path):
    app, port = server
    assert call(port, "POST", "/api/projects", {"path": str(tmp_path / "nope.mp4")}, token=app.token)[0] == 400
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    assert call(port, "POST", "/api/projects", {"path": str(txt)}, token=app.token)[0] == 400


def test_files_cannot_escape_the_project(server, video):
    app, port = server
    pid = app.store.from_path(str(video)).id
    for rel in ("../../secret.txt", "..%2F..%2Fsecret.txt", "project.json/../../x"):
        assert call(port, "GET", f"/media/{pid}/file/{rel}?t={app.token}")[0] == 404
    assert call(port, "GET", f"/media/{pid}/file/project.json?t={app.token}")[0] == 200
    assert call(port, "GET", "/api/projects/NOT_AN_ID/transcript", token=app.token)[0] == 404


def test_transcript_edit_replace_and_preview(server, video):
    app, port = server
    pid = app.store.from_path(str(video)).id
    assert call(port, "GET", f"/api/projects/{pid}/transcript", token=app.token)[0] == 400  # not transcribed
    seed_transcript(app, pid)
    status, _, body = call(port, "PUT", f"/api/projects/{pid}/transcript", {"format": 1}, token=app.token)
    assert status == 400  # invalid transcript refused
    status, _, body = call(port, "POST", f"/api/projects/{pid}/replace", {"pairs": ["Callan=Callum"]}, token=app.token)
    assert status == 200 and json.loads(body)["segments"][0]["words"][1]["text"] == "Callum."
    srt = (app.store.folder(pid) / "captions.srt").read_text(encoding="utf-8")
    assert "Callum." in srt
    status, _, body = call(port, "GET", f"/api/projects/{pid}/preview?style=bold", token=app.token)
    chunks = json.loads(body)
    assert status == 200 and chunks[0]["words"][0]["text"] == "HELLO"
    status, _, body = call(port, "GET", f"/api/projects/{pid}", token=app.token)
    d = json.loads(body)
    assert d["has_transcript"] and d["width"] == 640


def test_upload_creates_a_project(server, video):
    app, port = server
    status, _, body = call(port, "PUT", "/api/upload?name=my%20clip.mp4", token=app.token, raw=video.read_bytes())
    assert status == 200
    p = app.store.get(json.loads(body)["id"])
    assert p.uploaded and Path(p.source).read_bytes() == video.read_bytes()
    status, _, _ = call(port, "PUT", "/api/upload?name=evil.exe", token=app.token, raw=b"MZ")
    assert status == 400
    assert len(app.store.all()) == 1  # the refused upload left nothing behind


def test_clip_flow_find_select_without_render(server, video):
    app, port = server
    pid = app.store.from_path(str(video)).id
    long_words = [{"start": i * 0.4, "end": i * 0.4 + 0.3, "text": f"word{i}{'.' if i % 6 == 5 else ''}", "prob": 1}
                  for i in range(60)]
    actions.save_transcript(app.store, app.store.get(pid), {
        "format": 1, "language": "en", "duration": 30.0, "model": "t",
        "segments": [{"start": 0, "end": 24, "text": "x", "words": long_words}]})
    work = actions.job_find(app.store, app.store.get(pid), 5, 12)
    cands = work(lambda f, m="": None)
    assert cands
    picked = actions.apply_selection(app.store, app.store.get(pid),
                                     [{"id": cands[0]["id"], "title": "Best bit", "score": 9}])
    assert picked[0]["title"] == "Best bit" and picked[0]["source"] == "judge"
    assert actions.detail(app.store, app.store.get(pid))["picks"][0]["title"] == "Best bit"  # kept
    status, _, body = call(port, "POST", f"/api/projects/{pid}/ranking",
                           {"text": f'Sure!\n```json\n[{{"id": {cands[0]["id"]}}}]\n```'}, token=app.token)
    assert status == 200 and json.loads(body)[0]["id"] == cands[0]["id"]
    status, _, body = call(port, "GET", f"/api/projects/{pid}/judge?count=3", token=app.token)
    reply = json.loads(body)
    assert status == 200 and "Candidate" in reply["prompt"]
    assert Path(reply["judge_path"]).read_text(encoding="utf-8") == reply["prompt"]  # on disk for an AI agent
    status, _, body = call(port, "POST", f"/api/projects/{pid}/ranking", {"from_file": True}, token=app.token)
    assert status == 404 and "ranking.json" in json.loads(body)["error"]
    Path(reply["ranking_path"]).write_text(f'[{{"id": {cands[1]["id"]}, "title": "From disk"}}]', encoding="utf-8")
    status, _, body = call(port, "POST", f"/api/projects/{pid}/ranking", {"from_file": True}, token=app.token)
    assert status == 200 and json.loads(body)[0]["title"] == "From disk"


def test_waveform_and_frame_strip(server, video):
    app, port = server
    pid = app.store.from_path(str(video)).id
    status, _, body = call(port, "GET", f"/api/projects/{pid}/waveform", token=app.token)
    peaks = json.loads(body)
    assert status == 200 and len(peaks) == actions.WAVE_BUCKETS and 0 <= min(peaks) <= max(peaks) <= 1
    status, headers, jpg = call(port, "GET", f"/media/{pid}/strip?at=0.5,1.5,99&h=60&t={app.token}")
    assert status == 200 and headers["Content-Type"] == "image/jpeg" and jpg[:2] == b"\xff\xd8"
    assert call(port, "GET", f"/media/{pid}/strip?at=x&t={app.token}")[0] == 400
    assert call(port, "GET", f"/media/{pid}/strip?at=1")[0] == 403  # needs the token


def test_render_options_are_validated(server, video):
    app, port = server
    pid = app.store.from_path(str(video)).id
    for bad, msg in (({"title_look": "spin"}, "title look"), ({"emphasis": "loud"}, "emphasis"),
                     ({"title_secs": 500}, "title seconds")):
        with pytest.raises(ValueError, match=msg):
            actions.job_render(app.store, app.store.get(pid), [{"id": 1}], "bold", "auto",
                               **{"emphasis": "auto", "title_look": "banner", "title_secs": 3.5, **bad})



def test_hardening_headers_and_cross_site_fetch(server):
    app, port = server
    status, headers, _ = call(port, "GET", "/api/state", token=app.token)
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["Referrer-Policy"] == "no-referrer"
    assert call(port, "GET", "/api/state", token=app.token, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_only_builtin_style_names_are_accepted(server, video):
    app, port = server
    pid = app.store.from_path(str(video)).id
    seed_transcript(app, pid)
    status, _, body = call(port, "GET", f"/api/projects/{pid}/preview?style=C:/Windows/win.ini", token=app.token)
    assert status == 400 and "Unknown style" in json.loads(body)["error"]


def test_duplicate_jobs_conflict_and_queued_jobs_cancel():
    import time
    from cliptool.ui.jobs import Busy, Jobs

    jobs = Jobs()
    gate = threading.Event()

    def slow(progress):
        while not gate.wait(0.02):
            progress(0.5, "working")  # raises Cancelled once cancel() is called
        return "finished"

    first = jobs.submit("render", "p1", slow)
    with pytest.raises(Busy):
        jobs.submit("render", "p1", slow)
    queued = jobs.submit("burn", "p1", lambda progress: "never runs")
    assert jobs.cancel(queued.id).status == "error"
    jobs.cancel(first.id)
    for _ in range(100):
        if jobs.get(first.id).status != "running" and jobs.get(first.id).status != "queued":
            break
        time.sleep(0.02)
    assert jobs.get(first.id).error == "Cancelled."
    gate.set()
    later = jobs.submit("render", "p1", lambda progress: 42)  # allowed again once the first ended
    for _ in range(100):
        if jobs.get(later.id).status == "done":
            break
        time.sleep(0.02)
    assert jobs.get(later.id).result == 42 and jobs.get(queued.id).result is None
