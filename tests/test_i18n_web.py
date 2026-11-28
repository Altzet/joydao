"""Языки сайта и игры (static/i18n.js): словари целы, и у каждой русской фразы игры и обмена есть перевод.

Упал test_every_phrase_has_translation — в static/nunu.html или static/obmen.html появилась фраза без перевода:
добавь её в static/i18n/en.json и uk.json (ключ — русская фраза, {} — подставленное значение)."""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CYR = re.compile("[А-Яа-яЁё]")
P = "\x00"


def _dict(lang):
    return json.loads((ROOT / "static" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def _phrases(src: str) -> set[str]:
    """Как их видит i18n.js: строки и шаблоны JS, текст между тегами; ${…} → {}."""
    out: set[str] = set()

    def add(t):
        t = re.sub(r"\s+", " ", html.unescape(t)).strip()
        if CYR.search(t):
            out.add(t)

    def split(t):
        for m in re.finditer(r'(?:placeholder|title|aria-label|alt)="([^"]*)"', t):
            for x in m.group(1).split(P):
                add(x)
        for seg in re.split(r"<[^<>]*>", t):
            if P in seg:
                add(seg.replace(P, "{}"))
            for x in seg.split(P):
                add(x)

    def tpl(s, i):
        buf = []
        while True:
            c = s[i]
            if c == "\\":
                buf.append(s[i + 1]); i += 2; continue
            if c == "`":
                return i + 1, "".join(buf)
            if c == "$" and s[i + 1] == "{":
                i = js(s, i + 2, True); buf.append(P); continue
            buf.append(c); i += 1

    def js(s, i, inner=False):
        depth, prev = 0, ";"
        while i < len(s):
            c = s[i]
            if c in "\"'":
                j, buf = i + 1, []
                while s[j] != c and s[j] != "\n":
                    buf.append(s[j:j + 2] if s[j] == "\\" else s[j]); j += 2 if s[j] == "\\" else 1
                try:
                    split(json.loads('"' + "".join(buf).replace('"', '\\"').replace("\\'", "'") + '"'))
                except ValueError:
                    split("".join(buf))
                i, prev = j + 1, "a"; continue
            if c == "`":
                i, t = tpl(s, i + 1); split(t); prev = "a"; continue
            if s.startswith("//", i):
                i = s.index("\n", i); continue
            if s.startswith("/*", i):
                i = s.index("*/", i) + 2; continue
            if c == "/" and prev in "(,=:[!&|?{};+-*%<>~^":  # регулярное выражение
                j, cls = i + 1, False
                while not (s[j] == "/" and not cls) and s[j] != "\n":
                    cls = True if s[j] == "[" else False if s[j] == "]" else cls
                    j += 2 if s[j] == "\\" else 1
                i, prev = j + 1, "a"; continue
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0 and inner:
                    return i + 1
                depth -= 1
            if not c.isspace():
                prev = "a" if c.isalnum() or c in "_$)]" else c
            i += 1
        return i

    pos = 0
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", src, re.S):
        split(re.sub(r"<style.*?</style>", "", src[pos:m.start()], flags=re.S))
        js(m.group(1), 0)
        pos = m.end()
    split(re.sub(r"<style.*?</style>", "", src[pos:], flags=re.S))
    return out


def test_dictionaries_match():
    en, uk = _dict("en"), _dict("uk")
    assert en.keys() == uk.keys()
    for d in (en, uk):
        bad = [k for k, v in d.items() if not v or v.count("{}") != k.count("{}")]
        assert not bad, bad[:5]


def test_every_phrase_has_translation():
    en = _dict("en")
    for f in ("nunu.html", "obmen.html"):
        missing = sorted(p for p in _phrases((ROOT / "static" / f).read_text(encoding="utf-8")) if p not in en)
        assert not missing, f"{f}: нет перевода для {missing[:10]}"


def test_pages_load_translator(monkeypatch):
    from fastapi.testclient import TestClient
    from app import pause
    c = TestClient(pause.app)
    js = c.get("/i18n.js")
    assert js.status_code == 200 and "I18N_SET" in js.text and 'window.I18N_V||""' not in js.text
    assert c.get("/i18n/en.js").text.startswith("I18N_SET({")
    assert c.get("/i18n/xx.js").text == "I18N_SET({})"
    for path in ("/rules", "/uchastok", "/nunu", "/obmen", "/nope"):
        assert '<script src="/i18n.js"></script>' in c.get(path).text, path
    assert 'data-lang="uk"' in c.get("/rules").text  # переключатель в шапке сайта


def test_bot_language():
    from app import pause
    assert [pause.lang_of(x) for x in (None, "ru", "uk", "en-US", "de", "be")] == ["ru", "ru", "uk", "en", "en", "ru"]
    assert pause.TEXTS.keys() == pause.BTN.keys() == {"ru", "uk", "en"}
