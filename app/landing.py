"""Лендинг на главной: «чистый минимал». Всё содержимое — настройки из админки (раздел «Лендинг»),
здесь только вёрстка и тексты по умолчанию. Страница собирается на сервере: поисковики видят текст."""
from __future__ import annotations

import html
import re

from . import config

BLOCKS = {"hero": "Первый экран", "how": "Как это работает", "features": "Возможности", "cta": "Призыв", "faq": "Вопросы"}


def default() -> dict:
    ch = config.CHANNEL_ID.lstrip("@") if config.CHANNEL_ID.startswith("@") else "joydao"
    return {
        "brand": "Ну-ну",
        "seo": {"title": "Ну-ну — игра о том, насколько хорошо вы знаете друг друга",
                "desc": "Отвечай на карточки о себе, друг угадывает. Растёт твой остров, крепнут мосты. Играй в Telegram."},
        "links": {"play": f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}", "channel": f"https://t.me/{ch}",
                  "chat": config.COMMUNITY_CHAT_URL},
        "order": ["hero", "how", "features", "cta", "faq"],
        "off": [],
        "hero": {"kicker": "Игра в Telegram", "title": "Знаешь ли ты своих друзей?",
                 "sub": "Ответь на 10 карточек о себе — друг попробует угадать. Чем точнее вы понимаете друг друга, тем крепче мост между вашими островами.",
                 "cta": "Играть в Telegram", "cta2": "Как это работает", "img": ""},
        "how": {"title": "Как это работает", "items": [
            {"t": "Ответь о себе", "d": "10 карточек: от «чай или кофе» до того, как ты поступишь, когда всё идёт не по плану."},
            {"t": "Позови друга", "d": "Одна ссылка — и он отвечает так, как, по его мнению, ответил бы ты."},
            {"t": "Смотри, как растёт мост", "d": "Каждое совпадение — доска. От канатного моста до каменного."}]},
        "features": {"title": "Больше, чем угадайка", "items": [
            {"t": "Сад поступков", "d": "Каждый ответ сажает растение: цветы — когда идёшь к людям, деревья — когда берёшь паузу, скалы — когда стоишь за своё.", "img": ""},
            {"t": "Удиви себя", "d": "Дело на неделю с планом «если помеха — то действие». Можно вдвоём с другом.", "img": ""},
            {"t": "Мой участок", "d": "Нарисуй свой участок поверх снимка со спутника и пройдись по нему в 3D.", "img": ""},
            {"t": "Бутылка в море", "d": "Сыграй с незнакомцем. Мост появится, только если захотите оба.", "img": ""}]},
        "cta": {"title": "Сыграй с другом сегодня", "sub": "Бесплатно, прямо в Telegram, без регистрации.", "btn": "Открыть игру"},
        "faq": {"title": "Вопросы", "items": [
            {"q": "Это бесплатно?", "a": "Да. Игра работает внутри Telegram, ничего устанавливать не нужно."},
            {"q": "Нужно ли регистрироваться?", "a": "Нет. Telegram сам сообщает игре твоё имя — ни телефона, ни почты."},
            {"q": "Кто видит мои ответы?", "a": "Только тот, кто уже ответил сам: подсматривать до своего хода нельзя."},
            {"q": "Можно играть компанией?", "a": "Да: одна ссылка или QR-код на всех."}]},
        "footer": "Ну-ну · JoyDAO",
    }


def _str(x, n) -> bool:
    return isinstance(x, str) and len(x) <= n


def _url(x) -> bool:
    return isinstance(x, str) and (x == "" or re.fullmatch(r"(https://|/)[^\s\"'<>]{0,500}", x) is not None)


def check(lp) -> list[str]:
    """Только то, что безопасно вставить в страницу: строки разумной длины, ссылки https:// или свои."""
    e, d = [], default()
    if not isinstance(lp, dict):
        return ["Лендинг: настройки сломаны"]
    for k in ("hero", "how", "features", "cta", "faq", "seo", "links"):
        if not isinstance(lp.get(k), dict):
            return [f"Лендинг: нет раздела {k}"]
    if not (_str(lp.get("brand"), 40) and lp["brand"].strip()) or not _str(lp.get("footer"), 200):
        e.append("Лендинг: название — до 40 знаков")
    if not all(_str(lp["seo"].get(k), n) for k, n in (("title", 120), ("desc", 300))):
        e.append("Лендинг: заголовок для поиска до 120 знаков, описание до 300")
    if not all(_url(lp["links"].get(k)) for k in d["links"]) or not lp["links"].get("play"):
        e.append("Лендинг: ссылки должны начинаться с https://, ссылка «Играть» обязательна")
    if not isinstance(lp.get("order"), list) or sorted(lp["order"]) != sorted(BLOCKS) \
            or not isinstance(lp.get("off"), list) or any(k not in BLOCKS for k in lp["off"]):
        e.append("Лендинг: порядок блоков сломан")
    h = lp["hero"]
    if not all(_str(h.get(k), n) for k, n in (("kicker", 60), ("title", 120), ("sub", 400), ("cta", 40), ("cta2", 40))) \
            or not h.get("title", "").strip() or not h.get("cta", "").strip() or not _url(h.get("img", "")):
        e.append("Лендинг, первый экран: нужен заголовок и текст кнопки")
    for k, mx in (("how", 6), ("features", 8), ("faq", 20)):
        b, items = lp[k], lp[k].get("items")
        if not _str(b.get("title"), 80) or not isinstance(items, list) or len(items) > mx:
            e.append(f"Лендинг, «{BLOCKS[k]}»: до {mx} пунктов")
            continue
        for i, it in enumerate(items):
            a, z = ("q", "a") if k == "faq" else ("t", "d")
            if not isinstance(it, dict) or not (_str(it.get(a), 120) and it[a].strip()) or not _str(it.get(z), 600) \
                    or not _url(it.get("img", "")):
                e.append(f"Лендинг, «{BLOCKS[k]}» №{i + 1}: заполни заголовок")
    c = lp["cta"]
    if not all(_str(c.get(k), n) for k, n in (("title", 120), ("sub", 300), ("btn", 40))):
        e.append("Лендинг, призыв: слишком длинный текст")
    return e


E = html.escape


def _p(s: str) -> str:
    return "<br>".join(E(x) for x in s.split("\n"))


PHONE = """<div class="phone" aria-hidden="true"><div class="scr"><div class="ph-top"><b>Ну-ну</b><span>4 / 10</span></div>
<div class="ph-bar"><i style="width:40%"></i></div><div class="ph-c">Друзья</div>
<div class="ph-q">Ночь, костёр в горах, гитара по кругу. Доходит до тебя: «Давай, твоя очередь!»</div>
<div class="ph-o">Спою, как умею</div><div class="ph-o on">Передам гитару дальше</div><div class="ph-o">Попрошу научить аккорд</div>
<div class="ph-b">Дальше</div></div></div>"""

CSS = """*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font:17px/1.6 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#111;background:#fff;-webkit-font-smoothing:antialiased}
a{color:inherit}.w{max-width:1120px;margin:0 auto;padding:0 24px}
header{position:sticky;top:0;z-index:9;background:rgba(255,255,255,.86);backdrop-filter:saturate(1.6) blur(12px);-webkit-backdrop-filter:saturate(1.6) blur(12px);border-bottom:1px solid #F0F0EE}
header .w{display:flex;align-items:center;gap:28px;height:64px}.logo{font-weight:700;font-size:18px;letter-spacing:-.02em;text-decoration:none}
header nav{display:flex;gap:24px;margin-left:auto;font-size:15px}header nav a{text-decoration:none;color:#555}header nav a:hover{color:#111}
.btn{display:inline-flex;align-items:center;gap:8px;background:#111;color:#fff;text-decoration:none;font-weight:600;font-size:16px;padding:14px 22px;border-radius:12px;transition:transform .15s,background .15s}
.btn:hover{background:#1D7A5C;transform:translateY(-1px)}.btn.sm{font-size:14px;padding:9px 16px;border-radius:10px}.btn.g{background:#fff;color:#111;box-shadow:inset 0 0 0 1px #DADAD6}.btn.g:hover{background:#F6F6F4}
.hero{padding:84px 0 96px}.hero .w{display:grid;grid-template-columns:1.15fr .85fr;gap:56px;align-items:center}
.kick{display:inline-block;font-size:13px;font-weight:600;color:#1D7A5C;background:#E8F3EE;border-radius:20px;padding:5px 12px;margin-bottom:22px}
h1{font-size:clamp(42px,6.2vw,76px);line-height:1.02;letter-spacing:-.035em;margin:0;font-weight:700}
.lead{font-size:20px;color:#555;max-width:520px;margin:24px 0 34px}.acts{display:flex;gap:12px;flex-wrap:wrap}
.hero img{width:100%;border-radius:28px;display:block}
.phone{width:290px;margin:0 auto;border:10px solid #111;border-radius:44px;background:#F6F6F4;padding:22px 16px 26px;box-shadow:0 40px 80px -30px rgba(0,0,0,.35)}
.scr{display:flex;flex-direction:column;gap:10px}.ph-top{display:flex;justify-content:space-between;font-size:14px}.ph-top span{color:#888}
.ph-bar{height:5px;background:#E4E4E0;border-radius:3px}.ph-bar i{display:block;height:100%;background:#1D7A5C;border-radius:3px}
.ph-c{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#888;margin-top:6px}.ph-q{font-weight:600;font-size:16px;line-height:1.35}
.ph-o{background:#fff;border:1px solid #E6E6E2;border-radius:12px;padding:11px 12px;font-size:14px}.ph-o.on{border-color:#1D7A5C;background:#E8F3EE;color:#0F5A43;font-weight:600}
.ph-b{margin-top:6px;background:#111;color:#fff;text-align:center;border-radius:12px;padding:11px;font-size:14px;font-weight:600}
section{padding:96px 0}.alt{background:#F6F6F4}h2{font-size:clamp(32px,4vw,48px);letter-spacing:-.03em;line-height:1.08;margin:0 0 48px;font-weight:700}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:40px}.steps .n{font-size:15px;font-weight:700;color:#1D7A5C;margin-bottom:14px}
.steps h3,.cards h3{font-size:21px;letter-spacing:-.015em;margin:0 0 8px}.steps p,.cards p{margin:0;color:#555}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}.card{background:#fff;border-radius:20px;padding:28px;border:1px solid #ECECE8}
.card img{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:12px;margin-bottom:18px;display:block}
.cta{text-align:center}.cta h2{margin-bottom:14px}.cta p{color:#555;margin:0 0 30px;font-size:19px}
.faq{max-width:760px}details{border-bottom:1px solid #ECECE8;padding:20px 0}summary{cursor:pointer;font-weight:600;font-size:19px;list-style:none;display:flex;justify-content:space-between;gap:16px}
summary::-webkit-details-marker{display:none}summary:after{content:"+";font-weight:400;color:#888;font-size:24px;line-height:1}details[open] summary:after{content:"–"}details p{color:#555;margin:12px 0 0}
footer{border-top:1px solid #F0F0EE;padding:36px 0;font-size:15px;color:#777}footer .w{display:flex;gap:24px;flex-wrap:wrap;align-items:center}footer a{text-decoration:none}footer a:hover{color:#111}footer .sp{margin-left:auto}
.js .rv{opacity:0;transform:translateY(18px);transition:opacity .7s,transform .7s}.js .rv.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){.js .rv{opacity:1;transform:none;transition:none}html{scroll-behavior:auto}}
@media(max-width:860px){.hero{padding:48px 0 64px}.hero .w{grid-template-columns:1fr;gap:48px}header nav{display:none}header .btn{margin-left:auto}section{padding:72px 0}h2{margin-bottom:32px}.lead{font-size:18px}}"""


def render(lp: dict) -> str:
    L, off = lp["links"], set(lp.get("off") or [])
    play = E(L["play"])
    parts = []
    for k in lp["order"]:
        if k in off:
            continue
        b = lp[k]
        if k == "hero":
            pic = '<img src="%s" alt="">' % E(b["img"]) if b.get("img") else PHONE
            kick = '<span class="kick">%s</span>' % E(b["kicker"]) if b.get("kicker") else ""
            two = '<a class="btn g" href="#how">%s</a>' % E(b["cta2"]) if b.get("cta2") and "how" not in off else ""
            parts.append(f'<section class="hero"><div class="w"><div class="rv">{kick}<h1>{_p(b["title"])}</h1>'
                         f'<p class="lead">{_p(b["sub"])}</p><div class="acts"><a class="btn" href="{play}">{E(b["cta"])} →</a>'
                         f'{two}</div></div><div class="rv">{pic}</div></div></section>')
        elif k == "how" and b["items"]:
            parts.append(f'<section id="how"><div class="w"><h2 class="rv">{E(b["title"])}</h2><div class="steps">'
                         + "".join(f'<div class="rv"><div class="n">0{i + 1}</div><h3>{E(x["t"])}</h3><p>{_p(x["d"])}</p></div>'
                                   for i, x in enumerate(b["items"])) + "</div></div></section>")
        elif k == "features" and b["items"]:
            parts.append(f'<section id="features" class="alt"><div class="w"><h2 class="rv">{E(b["title"])}</h2><div class="cards">'
                         + "".join('<div class="card rv">%s<h3>%s</h3><p>%s</p></div>' % (
                             '<img src="%s" alt="" loading="lazy">' % E(x["img"]) if x.get("img") else "", E(x["t"]), _p(x["d"]))
                                   for x in b["items"]) + "</div></div></section>")
        elif k == "cta":
            sub = "<p>%s</p>" % _p(b["sub"]) if b.get("sub") else ""
            parts.append(f'<section class="cta"><div class="w rv"><h2>{_p(b["title"])}</h2>{sub}'
                         f'<a class="btn" href="{play}">{E(b["btn"] or "Играть")} →</a></div></section>')
        elif k == "faq" and b["items"]:
            parts.append(f'<section id="faq" class="alt"><div class="w faq"><h2 class="rv">{E(b["title"])}</h2>'
                         + "".join(f'<details class="rv"><summary>{E(x["q"])}</summary><p>{_p(x["a"])}</p></details>' for x in b["items"])
                         + "</div></section>")
    navs = [(k, t) for k, t in (("how", "Как это работает"), ("features", "Возможности"), ("faq", "Вопросы"))
            if k not in off and lp[k]["items"]]
    nav = "".join(f'<a href="#{k}">{E(lp[k]["title"] or t)}</a>' for k, t in navs)
    foot = "".join(f'<a href="{E(L[k])}">{t}</a>' for k, t in (("channel", "Канал"), ("chat", "Чат")) if L.get(k))
    seo = lp["seo"]
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(seo["title"] or lp["brand"])}</title><meta name="description" content="{E(seo["desc"])}">
<meta property="og:title" content="{E(seo["title"] or lp["brand"])}"><meta property="og:description" content="{E(seo["desc"])}"><meta property="og:type" content="website">
<meta name="theme-color" content="#ffffff"><script>document.documentElement.className="js"</script><style>{CSS}</style></head><body>
<header><div class="w"><a class="logo" href="#">{E(lp["brand"])}</a><nav>{nav}</nav><a class="btn sm" href="{play}">Открыть</a></div></header>
<main>{"".join(parts)}</main>
<footer><div class="w"><span>{E(lp["footer"])}</span>{foot}<a class="sp" href="{play}">Играть в Telegram →</a></div></footer>
<script>(()=>{{const o=new IntersectionObserver(es=>es.forEach(e=>{{if(e.isIntersecting){{e.target.classList.add("in");o.unobserve(e.target)}}}}),{{rootMargin:"0px 0px -8% 0px"}});
document.querySelectorAll(".rv").forEach(e=>o.observe(e))}})()</script></body></html>"""
