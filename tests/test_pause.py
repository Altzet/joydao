"""Режим паузы: заглушка на любой адрес, но игра «Ну-ну» доступна."""
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import pause  # noqa: E402


def test_pause_keeps_nunu():
    c = TestClient(pause.app)
    assert "Ну-ну" in c.get("/nunu").text
    assert "<h1>" in c.get("/anything").text  # вместо объявления о паузе — лендинг из админки
