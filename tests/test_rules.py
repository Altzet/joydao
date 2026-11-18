"""Свод правил: страница на сайте, короткая версия в игре, рассылка тем, кто ещё не принял (один раз)."""
import os

os.environ.setdefault("BOT_TOKEN", "1:test")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, nunu, pause, rules  # noqa: E402
from tests.test_nunu_server import init_data  # noqa: E402


def test_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    monkeypatch.setattr(config, "NUNU_ADMIN_KEY", "k" * 24)
    sent = []

    async def note(uid, text, url):
        sent.append(uid)
    monkeypatch.setattr(nunu, "_notify", note)
    monkeypatch.setattr(rules.asyncio, "sleep", lambda s: __import__("asyncio").sleep(0))
    c = TestClient(pause.app)
    p = c.get("/rules")
    assert p.status_code == 200 and "Свод правил" in p.text and "Жалобы и нарушения" in p.text
    assert "window.NUNU_RULES=" in c.get("/nunu").text
    assert 'href="/rules"' in c.get("/").text

    c.put("/nunu/api/state", json={"data": {"nunu-prefs": {"rules": {"v": rules.RULES_V}}}}, headers=init_data(1, "Принял"))
    c.put("/nunu/api/state", json={"data": {"nunu-prefs": {}}}, headers=init_data(2, "Старый"))
    h = {"X-Admin-Key": "k" * 24}
    s = c.get("/nunu/admin/api/rules", headers=h).json()
    assert (s["total"], s["ok"], s["todo"]) == (2, 1, 1)
    assert c.post("/nunu/admin/api/rules/broadcast", headers=h).json() == {"ok": True, "count": 1}
    assert sent == [2]
    assert c.get("/nunu/admin/api/rules", headers=h).json()["todo"] == 0  # второй раз тому же не шлём


def test_seo(tmp_path, monkeypatch):
    """robots, sitemap, canonical, микроразметка, честная 404 вместо копии главной."""
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    c = TestClient(pause.app)
    r = c.get("/robots.txt").text
    assert "Disallow: /nunu" in r and "sitemap.xml" in r
    assert "/rules</loc>" in c.get("/sitemap.xml").text
    h = c.get("/").text
    assert 'rel="canonical"' in h and '"@type": "SoftwareApplication"' in h and '"FAQPage"' in h
    assert c.get("/chto-ugodno").status_code == 404 and c.get("/").status_code == 200
    assert 'name="robots" content="noindex"' in c.get("/nunu").text


def test_og(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    c = TestClient(pause.app)
    r = c.get("/og.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert '/og.png"' in c.get("/").text


def test_seo_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(nunu, "DB", tmp_path / "n.db")
    c = TestClient(pause.app)
    for slug, word in (("uchastok", "Планировщик участка"), ("esli-to", "если — то")):
        r = c.get(f"/{slug}")
        assert r.status_code == 200 and word in r.text and f'/{slug}"' in r.text and '"FAQPage"' in r.text
        assert f"/{slug}</loc>" in c.get("/sitemap.xml").text
    assert c.get("/img/uchastok-3d.webp").status_code == 200 and c.get("/img/../app/config.py").status_code == 404
    assert 'href="/uchastok"' in c.get("/").text
