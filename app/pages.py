"""SEO-страницы сайта: отдельная страница под каждую группу поисковых запросов.

/uchastok — «планировщик участка онлайн, 3D» (раздел «Мой участок»);
/esli-to  — «план если — то, как достичь цели» (на чём держится «Удиви себя»).
Текст здесь, в коде; меняется вместе с приложением. Картинки — static/img (снимки экрана приложения).
"""
import html
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from . import landing

IMG = Path(__file__).resolve().parent.parent / "static" / "img"
CSS = """main.r{max-width:760px;margin:0 auto;padding:40px 20px 80px}main.r h1{font-size:2.2rem;line-height:1.15;margin:.2em 0 .4em}
main.r h2{font-size:1.4rem;margin:2em 0 .6em}main.r p,main.r li{font-size:17px;line-height:1.6}main.r .lead{font-size:19px;color:var(--muted)}
main.r figure{margin:1.6em 0}main.r figure img{width:100%;height:auto;border-radius:14px;border:1px solid var(--line)}
main.r figcaption{color:var(--muted);font-size:14px;margin-top:6px}main.r .two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){main.r .two{grid-template-columns:1fr}}main.r table{border-collapse:collapse;width:100%;font-size:15px}
main.r td,main.r th{border-bottom:1px solid var(--line);padding:8px 10px 8px 0;text-align:left;vertical-align:top}
main.r .go{display:inline-block;margin:1.4em 0 .4em}main.r .src{color:var(--muted);font-size:14px}main.r .src li{font-size:14px}"""

PAGES = {
    "uchastok": {
        "title": "Планировщик участка онлайн — чертёж и 3D в Telegram · Ну-ну",
        "desc": "Нарисуйте схему дачного участка в метрах, расставьте дом, грядки, теплицу и деревья, посмотрите тени по часам и пройдитесь по участку в 3D. Прямо в Telegram, без установки.",
        "h1": "Планировщик участка онлайн: чертёж в метрах и прогулка в 3D",
        "img": "/img/uchastok-3d.webp",
        "body": """<p class="lead">«Мой участок» — планировщик дачного участка внутри Telegram. Ничего не нужно устанавливать: открываете бота, задаёте размеры участка и расставляете постройки, грядки и деревья. Потом можно пройтись по участку в 3D и посмотреть, где будет тень в разное время дня.</p>
<div class="two"><figure><img src="/img/uchastok-plan.webp" alt="Схема дачного участка в метрах: дом, грядки, теплица, деревья" width="620" height="510" loading="lazy"><figcaption>Чертёж участка в метрах</figcaption></figure>
<figure><img src="/img/uchastok-3d.webp" alt="Прогулка по участку в 3D: дом, высокая грядка, беседка" width="684" height="740" loading="lazy"><figcaption>Тот же участок в 3D</figcaption></figure></div>
<h2>Что умеет планировщик</h2>
<table><tr><th>Задача</th><th>Как это сделано</th></tr>
<tr><td>Схема участка с реальными размерами</td><td>Участок любой формы: число углов, длины сторон и диагоналей в метрах. Сетка 1 м, компас, масштабная линейка, рулетка для замеров.</td></tr>
<tr><td>Расставить постройки и посадки</td><td>Около 30 видов объектов: дом, сарай, мастерская, теплица, беседка, навес, гараж, грядки и тёплые грядки, плодовые деревья, кусты, колодец, септик, забор, дорожки. Можно добавить своё.</td></tr>
<tr><td>Посмотреть в объёме</td><td>Прогулка по участку от первого лица. У построек задаются этажи, мансарда, крыша (двускатная, вальмовая, односкатная, арка, купол), стены или только каркас, окна и двери.</td></tr>
<tr><td>Тени и солнце</td><td>Солнце и тени по сезону и часу — видно, где будет светло для грядок и теплицы, а где тень для отдыха.</td></tr>
<tr><td>Привязка к местности</td><td>Спутниковый снимок под чертежом и уклон участка по открытым данным рельефа.</td></tr>
<tr><td>Не потерять</td><td>Всё хранится в вашем аккаунте и открывается с любого устройства, где есть Telegram.</td></tr></table>
<h2>Как спланировать участок за вечер</h2>
<ol><li><b>Задайте форму.</b> Число углов и длины сторон — по документам или рулеткой. Для неровного участка добавьте диагонали.</li>
<li><b>Поставьте то, что уже есть:</b> дом, колодец, деревья, забор. Положение задаётся двумя отступами от сторон — как при замере в жизни.</li>
<li><b>Посмотрите тени.</b> Передвиньте время суток и сезон: где солнце, туда — грядки и теплица.</li>
<li><b>Добавьте задуманное</b> и пройдитесь по участку в 3D. Неудачное место видно сразу, пока ничего не построено.</li>
<li><b>Посадите намерение.</b> Долгое нажатие на карту — и дело («поставить теплицу», «разбить грядки») появится ростком в этом месте и напомнит о себе.</li></ol>
<h2>Чем отличается от программ ландшафтного дизайна</h2>
<p>Это не профессиональный редактор с библиотекой тысяч растений и сметой. Это быстрый чертёж своего участка, который всегда в телефоне, плюс план дел прямо на карте. Друзья, с которыми вы играете в «Ну-ну», могут прийти в гости на участок — посмотреть карту и 3D, оставить совет у объекта или предложить общее дело.</p>""",
        "faq": [("Нужно ли что-то устанавливать?", "Нет. Планировщик работает внутри Telegram."),
                ("Можно ли нарисовать участок неправильной формы?", "Да. Задаётся любое число углов, длины сторон и диагонали; постройки тоже могут быть круглыми или многоугольными."),
                ("Видно ли тени от построек и деревьев?", "Да, по сезону и часу — на карте и в 3D."),
                ("Кто видит мой участок?", "Только вы. Друзья с мостом в игре могут зайти в гости, пока вы не закрыли участок в настройках.")],
    },
    "esli-to": {
        "title": "План «если — то»: как достичь цели и не бросить — метод Голлвитцера · Ну-ну",
        "desc": "Что такое план «если — то» (реализационные намерения), почему он помогает доводить цели до конца, как его составить и примеры. На этом методе построено приложение «Ну-ну».",
        "h1": "План «если — то»: как довести цель до дела",
        "img": "/og.png",
        "body": """<p class="lead">Большинство целей срываются не потому, что человек не хочет, а потому что в нужный момент он забывает, отвлекается или находит причину отложить. План «если — то» заранее связывает конкретную ситуацию с конкретным действием — и в этой ситуации действие запускается почти само.</p>
<h2>Что это такое</h2>
<p>Психолог Питер Голлвитцер назвал такие планы <b>реализационными намерениями</b> (implementation intentions). Формула простая: <b>«Если наступит ситуация X, то я сделаю Y»</b>. Цель отвечает на вопрос «чего я хочу», а план «если — то» — на вопросы «когда, где и как я это сделаю».</p>
<h2>Почему это работает</h2>
<p>Когда ситуация заранее связана с действием, мозгу не нужно в моменте вспоминать о цели и заново решать — ситуация сама служит сигналом. Метаанализ Голлвитцера и Ширана 2006 года объединил 94 независимых исследования: у тех, кто составлял такие планы, цели достигались заметно чаще — эффект средней-большой силы (d ≈ 0,65).</p>
<p>Ещё сильнее метод работает вместе с вопросом «что мне может помешать?». Сначала представить желаемое, потом честно назвать главную помеху — и уже под неё составить «если — то». Этот подход исследовала Габриэле Эттинген (методика WOOP).</p>
<h2>Как составить свой план</h2>
<ol><li><b>Цель.</b> Одна, своими словами: «каждый день гулять 30 минут».</li>
<li><b>Помеха.</b> Что обычно мешает: «прихожу уставший и сажусь в телефон».</li>
<li><b>Если — то.</b> Ситуация помехи + действие: «Если я пришёл домой и потянулся к телефону, то сразу надеваю кроссовки и выхожу на 10 минут».</li>
<li><b>Проверка.</b> Ситуация должна быть узнаваемой (время, место, событие), действие — маленьким и конкретным.</li></ol>
<h2>Примеры</h2>
<table><tr><th>Цель</th><th>Если…</th><th>…то</th></tr>
<tr><td>Читать больше</td><td>Если я лёг в кровать</td><td>то читаю 5 страниц до телефона</td></tr>
<tr><td>Пить воду</td><td>Если я налил себе кофе</td><td>то сначала выпиваю стакан воды</td></tr>
<tr><td>Не срываться в споре</td><td>Если чувствую, что повышаю голос</td><td>то делаю вдох и спрашиваю «что ты имеешь в виду?»</td></tr>
<tr><td>Довести огород</td><td>Если в выходные сухо и тепло</td><td>то первым делом вскапываю одну грядку</td></tr></table>
<h2>Как это устроено в «Ну-ну»</h2>
<p>В приложении большая цель — это семя, которое вы сажаете. Каждую неделю — одно дело к ней в форме «если помеха — то действие», с напоминанием в выбранный день и час. Сделали — отмечаете «Так и сделал», и в саду вырастает растение. Не получилось — ничего не сгорает: можно отметить «сделал иначе» и посадить новое. Дело можно привязать к месту на карте своего участка и к погоде: бот напомнит, когда выдастся подходящий день.</p>
<p class="src"><b>Источники:</b></p><ul class="src">
<li><a href="https://www.socmot.uni-konstanz.de/sites/default/files/99_Gollwitzer_Implementation_Intentions.pdf" rel="nofollow noopener">Gollwitzer P. M. (1999). Implementation intentions: Strong effects of simple plans. American Psychologist, 54(7).</a></li>
<li><a href="https://doi.org/10.1016/S0065-2601(06)38002-1" rel="nofollow noopener">Gollwitzer P. M., Sheeran P. (2006). Implementation intentions and goal achievement: A meta-analysis of effects and processes. Advances in Experimental Social Psychology, 38.</a></li>
<li><a href="https://woopmylife.org/" rel="nofollow noopener">Oettingen G. — WOOP (мысленное противопоставление + «если — то»).</a></li></ul>""",
        "faq": [("Чем «если — то» отличается от обычного плана?", "Обычный план говорит, что сделать. «Если — то» говорит, в какой момент: действие привязано к узнаваемой ситуации, поэтому реже забывается."),
                ("Сколько планов «если — то» составлять?", "Лучше один-два на цель. Много планов сразу размывают внимание."),
                ("Что делать, если план не сработал?", "Это сигнал, что ситуация или действие выбраны неудачно. Поменяйте одно из них — без самокритики.")],
    },
}


BASE = PAGES  # для админки: встроенный текст


def render(slug: str) -> HTMLResponse:
    if slug not in PAGES:
        raise HTTPException(404)
    from . import admin_more
    p = {**PAGES[slug], **admin_more.get("pages").get(slug, {})}  # правки из админки поверх текста из кода
    E, url, play = html.escape, f"{landing.SITE}/{slug}", "/nunu"
    faq = "".join(f"<h3>{E(q)}</h3><p>{E(a)}</p>" for q, a in p["faq"])
    ld = [{"@context": "https://schema.org", "@type": "Article", "headline": p["h1"], "description": p["desc"], "inLanguage": "ru",
           "image": landing.absu(p["img"]), "mainEntityOfPage": url,
           "publisher": {"@type": "Organization", "name": "JoyDAO · Ну-ну", "url": landing.SITE + "/"}},
          {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
              {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in p["faq"]]}]
    ldj = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    other = "".join(f'<a href="/{k}">{E(v["h1"])}</a><br>' for k, v in PAGES.items() if k != slug)
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">{landing.I18N}<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(p["title"])}</title><meta name="description" content="{E(p["desc"])}"><link rel="canonical" href="{url}">
<meta property="og:type" content="article"><meta property="og:title" content="{E(p["h1"])}"><meta property="og:description" content="{E(p["desc"])}">
<meta property="og:url" content="{url}"><meta property="og:image" content="{landing.absu(p["img"])}"><meta property="og:locale" content="ru_RU"><meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{ldj}</script>
{landing.ICON}<style>{landing.CSS}{landing.HEADER_CSS}{CSS}</style></head><body>
{landing.header("/" + slug)}
<main class="r"><h1>{E(p["h1"])}</h1>{p["body"]}<p><a class="btn go" href="{play}">Играть в «Ну-ну»</a></p>
<h2>Вопросы</h2>{faq}<h2>Ещё</h2><p>{other}<a href="/rules">Правила «Ну-ну»</a></p></main>
<footer><div class="w"><span>JoyDAO · Ну-ну</span><a class="sp" href="{play}">Играть →</a></div></footer>{landing.tg_js()}</body></html>""")


def img(name: str) -> FileResponse:
    f = IMG / name
    if "/" in name or not f.is_file() or f.suffix not in (".webp", ".png", ".jpg"):
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=604800"})
