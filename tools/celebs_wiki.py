"""Собрать app/data/celebs.json — знаменитости с датами рождения из Википедии (Wikidata P569)
и фото из Wikimedia Commons (P18) — с автором и лицензией: CC BY/BY-SA требуют подписи.

Запуск на сервере (там есть интернет): .venv/bin/python tools/celebs_wiki.py
Добавить звезду — допиши строку в LIST: заголовок статьи в английской Википедии + категория.
Политиков сознательно нет: клуб про людей, а не про споры.
"""
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "celebs.json"
LANGS = ("ru", "uk", "en")

# категория: заголовки статей en.wikipedia
LIST = {
    "biz": ["Elon Musk", "Steve Jobs", "Bill Gates", "Mark Zuckerberg", "Jeff Bezos", "Pavel Durov",
            "Oprah Winfrey", "Warren Buffett", "Coco Chanel"],
    "music": ["Taylor Swift", "Beyoncé", "Rihanna", "Billie Eilish", "Lady Gaga", "Ed Sheeran", "Dua Lipa",
              "The Weeknd", "Freddie Mercury", "Michael Jackson", "Jamala", "Tina Karol", "Max Barskih"],
    "film": ["Leonardo DiCaprio", "Brad Pitt", "Angelina Jolie", "Johnny Depp", "Keanu Reeves",
             "Scarlett Johansson", "Zendaya", "Margot Robbie", "Ryan Gosling", "Tom Holland",
             "Timothée Chalamet", "Mila Kunis", "Milla Jovovich", "Jennifer Aniston"],
    "sport": ["Cristiano Ronaldo", "Lionel Messi", "Serena Williams", "Oleksandr Usyk", "Andriy Shevchenko",
              "Elina Svitolina", "Usain Bolt", "Michael Jordan"],
    "mind": ["Albert Einstein", "Marie Curie", "Nikola Tesla", "Frida Kahlo"],
}

# Один SPARQL-запрос: только дата (с точностью до дня, григорианская), имена и ссылки — килобайты, а не мегабайты
QUERY = """SELECT ?t ?item ?b ?en ?ru ?uk ?wen ?wru ?wuk ?img WHERE {
  VALUES ?t { %s }
  ?wen schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?t.
  ?item p:P569/psv:P569 [wikibase:timeValue ?b; wikibase:timePrecision 11;
                         wikibase:timeCalendarModel wd:Q1985727].
  OPTIONAL { ?item rdfs:label ?en FILTER(lang(?en)="en") }
  OPTIONAL { ?item rdfs:label ?ru FILTER(lang(?ru)="ru") }
  OPTIONAL { ?item rdfs:label ?uk FILTER(lang(?uk)="uk") }
  OPTIONAL { ?wru schema:about ?item; schema:isPartOf <https://ru.wikipedia.org/> }
  OPTIONAL { ?wuk schema:about ?item; schema:isPartOf <https://uk.wikipedia.org/> }
  OPTIONAL { ?item wdt:P18 ?img }
}"""


UA = {"User-Agent": "JoyDao/1.0 (github.com/Altzet/joydao)"}
FREE = ("CC0", "Public domain", "CC BY", "CC-BY")  # CC BY и CC BY-SA любых версий; NC/ND и «fair use» — мимо


def get(url: str, **params) -> dict:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={**UA, "Accept": "application/sparql-results+json, application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def photos(files: list[str]) -> dict[str, dict]:
    """Файлы Commons → {файл: {url превью, автор, лицензия, страница}} — только со свободной лицензией."""
    out = {}
    for i in range(0, len(files), 50):
        q = get("https://commons.wikimedia.org/w/api.php", action="query", format="json", prop="imageinfo",
                iiprop="url|extmetadata", iiurlwidth=330, titles="|".join("File:" + f for f in files[i:i + 50]))
        for pg in q["query"]["pages"].values():
            ii = (pg.get("imageinfo") or [{}])[0]
            meta = {k: re.sub(r"<[^>]+>", "", str(v.get("value", ""))).strip() for k, v in ii.get("extmetadata", {}).items()}
            lic = meta.get("LicenseShortName", "")
            if ii.get("thumburl") and lic.startswith(FREE) and "NC" not in lic and "ND" not in lic:
                out[pg["title"].removeprefix("File:")] = {"url": ii["thumburl"], "author": meta.get("Artist", "")[:80],
                                                         "license": lic, "page": ii["descriptionurl"]}
    return out


def fetch(titles: list[str]) -> list[dict]:
    q = QUERY % " ".join(json.dumps(t, ensure_ascii=False) + "@en" for t in titles)
    return [{k: v["value"] for k, v in row.items()} for row in get("https://query.wikidata.org/sparql", query=q)["results"]["bindings"]]


def build(rows: list[dict]) -> dict[str, dict]:
    """Строки SPARQL → {заголовок: запись}. Несколько дат у одного человека — берём первую."""
    out = {}
    for r in rows:
        if r["t"] in out or not r["b"].startswith(("1", "2")):  # до н. э. и мусор — мимо
            continue
        out[r["t"]] = {"id": r["item"].rsplit("/", 1)[1],
                       "name": {l: r.get(l) or r.get("en") or r["t"] for l in LANGS},
                       "birth": r["b"][:10],
                       "wiki": {l: r["w" + l] for l in LANGS if r.get("w" + l)},
                       # P18 приходит ссылкой …/Special:FilePath/Имя_файла.jpg
                       "img": urllib.parse.unquote(r["img"].rsplit("/", 1)[1]).replace("_", " ") if r.get("img") else None}
    return out


def main():
    out, skipped = [], []
    titles = [t for ts in LIST.values() for t in ts]
    print(f"Запрашиваю Wikidata: {len(titles)} звёзд…", flush=True)
    found = build(fetch(titles))
    print(f"Фото из Wikimedia Commons…", flush=True)
    ph = photos(sorted({c["img"] for c in found.values() if c["img"]}))
    for c in found.values():
        c["photo"] = ph.get(c.pop("img"))
    for cat, ts in LIST.items():
        for t in ts:
            (out.append({**found[t], "cat": cat}) if t in found else skipped.append(t))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ {len(out)} звёзд, с фото {sum(bool(c['photo']) for c in out)} → {OUT}")
    if skipped:
        print("⚠️ Не нашлись или без точной даты (проверь заголовок статьи):", ", ".join(skipped))


if __name__ == "__main__":
    main()
