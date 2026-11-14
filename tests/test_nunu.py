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
    for marker in ("const C0=[", "const C1=[", "const DEEP=[", "Проехали", "function bridge(", "function stepWeek(", "Чем удивишь себя"):
        assert marker in t
    assert "очков из" not in t


def test_my_plot():
    """«Мой участок» (раздел «Динамическое равновесие»): вкладка в меню, анкета уходит на сервер вместе с садом."""
    t = TestClient(api.app).get("/nunu").text
    for marker in ('["plot","Участок"', "function plotHome(", '"nunu-plot":plot', "DeviceOrientation", "LocationManager",
                   "function plotMap(", "function pSun(", "function pMode(", "function pEdgeSeg(", "function pSlope(", "function merge3(", "base:sync.rev", "function markSeed(", "loc?plotMap({fresh:", "function pPoly(", "function pPlace(", "function pMeasure(", "/nunu/api/tile/", "function pMig(", "function plot3d(", "three@0.170.0", "function pD3Panel(", "function pWinAuto(", "function pDwTap(", "function pRoofSpan(", "const DECK1=", "NUNU_CFG", "function goalTree(", "nunu-goals"):
        assert marker in t
