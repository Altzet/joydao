"""Собрать app/data/celebs.json — знаменитости с датами рождения из Википедии (Wikidata P569).

Запуск на сервере (там есть интернет): .venv/bin/python tools/celebs_wiki.py
Добавить звезду — допиши строку в LIST: заголовок статьи в английской Википедии + категория.
Политиков сознательно нет: клуб про людей, а не про споры.
"""
import json
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


def fetch(titles: list[str]) -> dict:
    q = urllib.parse.urlencode({"action": "wbgetentities", "sites": "enwiki", "titles": "|".join(titles),
                                "props": "claims|labels|sitelinks", "languages": "|".join(LANGS),
                                "sitefilter": "|".join(f"{l}wiki" for l in LANGS), "format": "json"})
    req = urllib.request.Request("https://www.wikidata.org/w/api.php?" + q,
                                 headers={"User-Agent": "JoyDao/1.0 (github.com/Altzet/joydao)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["entities"]


def birth(ent: dict) -> str | None:
    """P569 с точностью до дня (precision 11), григорианский календарь — иначе None."""
    for c in ent.get("claims", {}).get("P569", []):
        v = c["mainsnak"].get("datavalue", {}).get("value", {})
        if v.get("precision") == 11 and v.get("calendarmodel", "").endswith("Q1985727") and v["time"][0] == "+":
            return v["time"][1:11]
    return None


def main():
    out, skipped = [], []
    for cat, titles in LIST.items():
        ents = fetch(titles)
        by_title = {e.get("sitelinks", {}).get("enwiki", {}).get("title"): e for e in ents.values() if "missing" not in e}
        for t in titles:
            e, b = by_title.get(t), None
            if e:
                b = birth(e)
            if not b:
                skipped.append(t)
                continue
            lab = {l: e["labels"].get(l, e["labels"].get("en", {})).get("value", t) for l in LANGS}
            wiki = {l: f"https://{l}.wikipedia.org/wiki/" + urllib.parse.quote(s["title"].replace(" ", "_"))
                    for l in LANGS if (s := e["sitelinks"].get(f"{l}wiki"))}
            out.append({"id": e["id"], "cat": cat, "name": lab, "birth": b, "wiki": wiki})
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ {len(out)} звёзд → {OUT}")
    if skipped:
        print("⚠️ Не нашлись или без точной даты (проверь заголовок статьи):", ", ".join(skipped))


if __name__ == "__main__":
    main()
