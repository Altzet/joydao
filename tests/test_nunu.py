"""Игра «Ну-ну» отдаётся как страница."""
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import api  # noqa: E402


def test_nunu_page():
    r = TestClient(api.app).get("/nunu")
    assert r.status_code == 200 and "Ну-ну" in r.text
