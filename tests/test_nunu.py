"""Игра «Ну-ну» отдаётся как страница."""
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import api  # noqa: E402


def test_nunu_page():
    r = TestClient(api.app).get("/nunu")
    assert r.status_code == 200 and "Ну-ну" in r.text


def test_nunu_stage1():
    """Этап 1: лестница уровней, «Проехали», мост вместо счёта, шаг недели."""
    t = TestClient(api.app).get("/nunu").text
    for marker in ("const C1=[", "const DEEP=[", "Проехали", "function bridge(", "function stepWeek(", "Новый вызов"):
        assert marker in t
    assert "очков из" not in t
