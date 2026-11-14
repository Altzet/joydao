"""Лендинг на главной: «чистый минимал». Всё содержимое — настройки из админки (раздел «Лендинг»),
здесь только вёрстка и тексты по умолчанию. Страница собирается на сервере: поисковики видят текст."""
from __future__ import annotations

import html
import re

from . import config

BLOCKS = {"hero": "Первый экран", "how": "Как это работает", "author": "От автора", "features": "Возможности",
          "science": "На чём это держится", "cta": "Призыв", "faq": "Вопросы"}


def default() -> dict:
    ch = config.CHANNEL_ID.lstrip("@") if config.CHANNEL_ID.startswith("@") else "joydao"
    return {
        "brand": "Ну-ну",
        "seo": {"title": "Ну-ну — каждое намерение вырастает",
                "desc": "Приложение для саморазвития в Telegram: цель, план на неделю «если помеха — то действие» и сад, где каждое намерение вырастает."},
        "links": {"play": f"https://t.me/{config.BOT_USERNAME}/{config.NUNU_APP}", "channel": f"https://t.me/{ch}",
                  "chat": config.COMMUNITY_CHAT_URL},
        "order": ["hero", "how", "author", "features", "science", "faq", "cta"],
        "off": [],
        "hero": {"kicker": "Приложение для саморазвития", "title": "Каждое намерение —\nсемя",
                 "sub": "Запиши, к чему идёшь. Составь план на неделю. Отмечай сделанное — и смотри, как намерение вырастает в дерево.",
                 "cta": "Посадить первое намерение", "cta2": "Как это работает", "img": ""},
        "how": {"title": "Как намерение вырастает", "items": [
            {"t": "Цель", "d": "Большая цель на месяцы — ствол твоего дерева. Запиши её и ответь себе, зачем она тебе."},
            {"t": "План «если — то»", "d": "Каждую неделю — одно дело. Заранее подумай, что может помешать, и реши, что сделаешь тогда."},
            {"t": "Поступок", "d": "Сделал — отметь. Каждое сделанное дело — новый шаг, а в саду появляется растение."},
            {"t": "Урожай", "d": "Шаг за шагом цель становится реальностью, и ты видишь, какой путь уже прошёл."}]},
        "author": {"title": "От автора", "text": "Привет! Ты на моём детище. Это приложение я создавал буквально для своих нужд: мне хотелось сделать что-то полезное и что-то весёлое.\n\nИ знаешь, оно действительно начало помогать. Когда вырастают по-настоящему классные семена намерений, моя жизнь становится счастливее. Поэтому пользуюсь сам и делюсь с вами, дорогие друзья.\n\nБудьте счастливы, до встречи в приложении!",
                   "sign": "Автор «Ну-ну»", "img": ""},
        "features": {"title": "Вместе легче", "items": [
            {"t": "Попутчики", "d": "Дело можно сделать вдвоём: друг получит его в свой сад, а мост между вами станет крепче.", "img": ""},
            {"t": "Узнай, как тебя видят", "d": "Ответь на карточки о себе — друг угадает. Так лучше понимаешь, кто ты и чего хочешь.", "img": ""},
            {"t": "Мой участок", "d": "Если твоя цель — свой дом и земля, нарисуй участок поверх снимка со спутника и пройдись по нему в 3D.", "img": ""}]},
        "science": {"title": "На чём это держится", "items": [
            {"t": "План «если — то»", "d": "Исследования Петера Гольвитцера: заранее продуманный ответ на помеху заметно повышает шанс довести дело до конца."},
            {"t": "Сначала помеха, потом план", "d": "Метод Габриэле Эттинген: честно посмотреть на то, что мешает, — и мечта превращается в действие."},
            {"t": "От лёгкого к личному", "d": "Вопросы для друзей идут, как в исследованиях Артура Арона: от простого к важному — так люди становятся ближе."}]},
        "cta": {"title": "Посади своё первое намерение", "sub": "Бесплатно, прямо в Telegram.", "btn": "Начать"},
        "faq": {"title": "Вопросы", "items": [
            {"q": "Это игра или работа над собой?", "a": "И то и другое. Весёлое помогает не бросить, полезное — дойти до цели."},
            {"q": "Чем это отличается от трекера привычек?", "a": "Трекер считает галочки. Здесь у каждого дела есть план на случай помехи, у цели — смысл, а результат растёт у тебя на глазах."},
            {"q": "А если я брошу через неделю?", "a": "Семя подождёт. Не вышло — отметь «сделал иначе» и посади новое. Ничего не сгорает."},
            {"q": "Кто видит мои цели?", "a": "Только ты. Друг видит лишь те дела, которые вы делаете вдвоём."},
            {"q": "Это бесплатно?", "a": "Да. Приложение работает внутри Telegram, ничего устанавливать не нужно."}]},
        "footer": "Ну-ну · JoyDAO",
    }


def norm(lp) -> dict:
    """Сохранённые раньше настройки + блоки, которых тогда не было (их порядок — как по умолчанию)."""
    if not isinstance(lp, dict):
        return lp
    d, lp = default(), dict(lp)
    for k in BLOCKS:
        if k not in lp:
            lp[k] = d[k]
    if isinstance(lp.get("order"), list):
        lp["order"] = [k for k in lp["order"] if k in BLOCKS] + [k for k in d["order"] if k not in lp["order"]]
    return lp


def _str(x, n) -> bool:
    return isinstance(x, str) and len(x) <= n


def _url(x) -> bool:
    return isinstance(x, str) and (x == "" or re.fullmatch(r"(https://|/)[^\s\"'<>]{0,500}", x) is not None)


def check(lp) -> list[str]:
    """Только то, что безопасно вставить в страницу: строки разумной длины, ссылки https:// или свои."""
    e, d = [], default()
    if not isinstance(lp, dict):
        return ["Лендинг: настройки сломаны"]
    lp = norm(lp)
    for k in (*BLOCKS, "seo", "links"):
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
    a = lp["author"]
    if not all(_str(a.get(k), n) for k, n in (("title", 80), ("text", 2000), ("sign", 80))) or not _url(a.get("img", "")):
        e.append("Лендинг, «От автора»: текст до 2000 знаков")
    for k, mx in (("how", 6), ("features", 8), ("science", 6), ("faq", 20)):
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


LEAF = '<svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true"><path d="M20 34V18" stroke="#1D7A5C" stroke-width="2.5" stroke-linecap="round"/><path d="M20 22c0-7 5-11 12-11 0 7-5 11-12 11zM20 26c0-5-4-8-10-8 0 5 4 8 10 8z" fill="#1D7A5C"/></svg>'

GROW = """<div class="grow" aria-hidden="true"><svg viewBox="0 0 360 380" width="100%">
<ellipse cx="180" cy="330" rx="150" ry="22" fill="#F1EFEA"/>
<ellipse class="g0" cx="180" cy="322" rx="10" ry="6" fill="#8A5A2B"/>
<path class="g1" d="M180 322 C180 270 176 220 180 150" stroke="#2B2B2B" stroke-width="7" fill="none" stroke-linecap="round"/>
<path class="g2" d="M180 250 C150 238 130 220 118 196" stroke="#2B2B2B" stroke-width="4" fill="none" stroke-linecap="round"/>
<path class="g2" d="M179 210 C206 196 226 178 238 150" stroke="#2B2B2B" stroke-width="4" fill="none" stroke-linecap="round"/>
<g class="g3"><circle cx="180" cy="128" r="44" fill="#1D7A5C"/><circle cx="124" cy="182" r="30" fill="#2E8F6E"/><circle cx="238" cy="138" r="32" fill="#2E8F6E"/>
<circle cx="150" cy="112" r="26" fill="#3FA07E"/><circle cx="212" cy="100" r="24" fill="#3FA07E"/></g>
<g class="g4"><circle cx="160" cy="140" r="7" fill="#E2533F"/><circle cx="214" cy="128" r="7" fill="#E2533F"/><circle cx="128" cy="190" r="6" fill="#E2533F"/><circle cx="244" cy="148" r="6" fill="#E2533F"/></g>
<g class="lbl" font-family="Inter,system-ui,sans-serif" font-size="13" fill="#555">
<text class="l0" x="200" y="330">намерение</text><text class="l1" x="190" y="262">план</text><text class="l2" x="80" y="232">поступок</text><text class="l3" x="268" y="96">рост</text></g></svg></div>"""

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
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:36px}.steps .n{font-size:15px;font-weight:700;color:#1D7A5C;margin-bottom:14px}
.steps h3,.cards h3{font-size:21px;letter-spacing:-.015em;margin:0 0 8px}.steps p,.cards p{margin:0;color:#555}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}.card{background:#fff;border-radius:20px;padding:28px;border:1px solid #ECECE8}
.card img{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:12px;margin-bottom:18px;display:block}
.cta{text-align:center}.cta h2{margin-bottom:14px}.cta p{color:#555;margin:0 0 30px;font-size:19px}
.faq{max-width:760px}details{border-bottom:1px solid #ECECE8;padding:20px 0}summary{cursor:pointer;font-weight:600;font-size:19px;list-style:none;display:flex;justify-content:space-between;gap:16px}
summary::-webkit-details-marker{display:none}summary:after{content:"+";font-weight:400;color:#888;font-size:24px;line-height:1}details[open] summary:after{content:"–"}details p{color:#555;margin:12px 0 0}
.grow{max-width:420px;margin:0 auto}@media(max-width:860px){.grow{max-width:300px}}.grow .g1{stroke-dasharray:200;stroke-dashoffset:200;animation:gd 1.2s .3s ease-out forwards}
.grow .g2{stroke-dasharray:120;stroke-dashoffset:120;animation:gd .8s 1.3s ease-out forwards}.grow .g3,.grow .g4{transform-box:fill-box;transform-origin:center bottom;transform:scale(0);animation:gs .7s 1.9s cubic-bezier(.3,1.4,.5,1) forwards}
.grow .g4{animation-delay:2.6s}.grow .lbl text{opacity:0;animation:gf .5s forwards}.grow .l0{animation-delay:.2s}.grow .l1{animation-delay:1s}.grow .l2{animation-delay:1.7s}.grow .l3{animation-delay:2.4s}
@keyframes gd{to{stroke-dashoffset:0}}@keyframes gs{to{transform:scale(1)}}@keyframes gf{to{opacity:1}}
.author{display:grid;grid-template-columns:96px 1fr;gap:36px;max-width:860px}.author .ava{width:96px;height:96px;border-radius:50%;object-fit:cover;background:#E8F3EE;display:flex;align-items:center;justify-content:center}
.author h2{margin-bottom:24px}.author p{font-size:20px;line-height:1.65;margin:0 0 18px;color:#222}.author .sign{font-size:16px;color:#777;margin-top:26px}
.sci{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:40px;border-top:1px solid #ECECE8;padding-top:36px}.sci h3{font-size:19px;margin:0 0 8px}.sci p{margin:0;color:#555}
@media(prefers-reduced-motion:reduce){.grow *{animation:none!important;stroke-dashoffset:0!important;transform:none!important;opacity:1!important}}
@media(max-width:860px){.author{grid-template-columns:1fr;gap:20px}.author p{font-size:18px}}
footer{border-top:1px solid #F0F0EE;padding:36px 0;font-size:15px;color:#777}footer .w{display:flex;gap:24px;flex-wrap:wrap;align-items:center}footer a{text-decoration:none}footer a:hover{color:#111}footer .sp{margin-left:auto}
.js .rv{opacity:0;transform:translateY(18px);transition:opacity .7s,transform .7s}.js .rv.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){.js .rv{opacity:1;transform:none;transition:none}html{scroll-behavior:auto}}
@media(max-width:860px){.hero{padding:48px 0 64px}.hero .w{grid-template-columns:1fr;gap:48px}header nav{display:none}header .btn{margin-left:auto}section{padding:72px 0}h2{margin-bottom:32px}.lead{font-size:18px}}"""


def render(lp: dict) -> str:
    lp = norm(lp)
    L, off = lp["links"], set(lp.get("off") or [])
    play = E(L["play"])
    parts = []
    for k in lp["order"]:
        if k in off:
            continue
        b = lp[k]
        if k == "hero":
            pic = '<img src="%s" alt="">' % E(b["img"]) if b.get("img") else GROW
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
        elif k == "author" and b.get("text", "").strip():
            ph = '<img class="ava" src="%s" alt="">' % E(b["img"]) if b.get("img") else '<div class="ava">%s</div>' % LEAF
            parts.append(f'<section id="author"><div class="w author rv">{ph}<div><h2>{E(b["title"])}</h2>'
                         + "".join(f"<p>{E(x)}</p>" for x in b["text"].split("\n") if x.strip())
                         + (f'<p class="sign">{E(b["sign"])}</p>' if b.get("sign") else "") + "</div></div></section>")
        elif k == "science" and b["items"]:
            parts.append(f'<section id="science"><div class="w"><h2 class="rv">{E(b["title"])}</h2><div class="sci">'
                         + "".join(f'<div class="rv"><h3>{E(x["t"])}</h3><p>{_p(x["d"])}</p></div>' for x in b["items"])
                         + "</div></div></section>")
        elif k == "cta":
            sub = "<p>%s</p>" % _p(b["sub"]) if b.get("sub") else ""
            parts.append(f'<section class="cta"><div class="w rv"><h2>{_p(b["title"])}</h2>{sub}'
                         f'<a class="btn" href="{play}">{E(b["btn"] or "Играть")} →</a></div></section>')
        elif k == "faq" and b["items"]:
            parts.append(f'<section id="faq" class="alt"><div class="w faq"><h2 class="rv">{E(b["title"])}</h2>'
                         + "".join(f'<details class="rv"><summary>{E(x["q"])}</summary><p>{_p(x["a"])}</p></details>' for x in b["items"])
                         + "</div></section>")
    navs = [(k, t) for k, t in (("how", "Как это работает"), ("author", "От автора"), ("faq", "Вопросы"))
            if k not in off and (lp[k].get("items") or lp[k].get("text"))]
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
