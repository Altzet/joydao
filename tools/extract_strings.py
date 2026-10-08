"""Собрать все русские строки, которым нужен перевод: T("...") в Python,
данные-словари (энергии, тексты дня, разборы) и строки приложения (index.html).
Запуск: BOT_TOKEN=1:x python tools/extract_strings.py > tools/strings.json"""
import ast, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CYR = re.compile(r"[А-Яа-яЁё]")
out: list[str] = []
add = lambda s: s and CYR.search(s) and s not in out and out.append(s)  # noqa: E731

# 1) Python: первый аргумент T("…")
for f in sorted((ROOT / "app").glob("*.py")):
    for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "T" and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            add(node.args[0].value)
        # T("a" if x else "b")
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "T" and node.args \
                and isinstance(node.args[0], ast.IfExp):
            for b in (node.args[0].body, node.args[0].orelse):
                if isinstance(b, ast.Constant):
                    add(b.value)

# 2) Данные, которые переводятся через T(переменная)
from app import bot, compat, mailing, suisai, teaser  # noqa: E402
def walk(x):
    if isinstance(x, str): add(x)
    elif isinstance(x, dict): [walk(v) for v in x.values()]
    elif isinstance(x, (list, tuple)): [walk(v) for v in x]
for data in (suisai.CHS_SHORT, suisai.ENERGY_NAMES, suisai.TEXT_CALC_MEANINGS, mailing.DAY_TEXTS,
             teaser.HOOKS, teaser.TEXTS, teaser.ROLES, teaser.KINDS, bot.GOAL_LABELS,
             bot.PAIR_INVITE_TEXT, [w["note"] for w in bot.CRYPTO_WALLETS.values()],
             compat.RULES["planet_of"]):
    walk(data)

# 3) Приложение: строковые литералы с кириллицей в скрипте + статичная разметка
html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
head, script = html.split("<script>", 1)[0], html.split("<script>", 1)[1].split("</script>")[0]
body = head.split("<body>", 1)[1]
for m in re.finditer(r"'((?:[^'\\\n]|\\.)*)'", script):
    add(m.group(1).replace("\\'", "'"))
for m in re.finditer(r">([^<>]+)<", body):
    add(m.group(1).strip())
for m in re.finditer(r'(?:aria-label|placeholder)="([^"]+)"', body):
    add(m.group(1))

# 4) Описание бота для других языков
from app import main  # noqa: E402
add(main.BOT_DESCRIPTION); add(main.BOT_SHORT_DESCRIPTION)

# Контроль: кириллица в шаблонных строках вне ${…} — значит, забыли T()
leftover = []
for m in re.finditer(r"`([^`]*)`", script):
    raw = re.sub(r"\$\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", m.group(1))
    if CYR.search(raw):
        leftover.append(raw.strip()[:80])
print(json.dumps({"strings": out, "leftover": leftover}, ensure_ascii=False, indent=1))
