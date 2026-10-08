"""Собрать app/i18n/<lang>.json из переводов tools/tr/<lang>.txt.

Формат перевода: одна строка на фразу — «N<TAB>"перевод в JSON"», где N — номер
фразы в tools/strings.json. Проверяем, что переведены все фразы и что в каждой
сохранены плейсхолдеры {…} и HTML-теги — иначе бот сломает разметку.
Запуск: python tools/build_i18n.py"""
import json, re, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = json.loads((ROOT / "tools" / "strings.json").read_text(encoding="utf-8"))["strings"]
ph = lambda s: Counter(re.findall(r"\{\w+\}", s))              # noqa: E731
tags = lambda s: Counter(re.findall(r"</?\w+[^>]*>", s))       # noqa: E731
ok = True
langs = sorted({f.stem.split("_")[0] for f in (ROOT / "tools" / "tr").glob("*.txt")})
for lang in langs:  # перевод языка может лежать в нескольких файлах: uk_1.txt, uk_2.txt…
    out, errs = {}, []
    lines = [l for f in sorted((ROOT / "tools" / "tr").glob(f"{lang}_*.txt"))
             for l in f.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        if not line.strip():
            continue
        n, _, val = line.partition("\t")
        n, val = int(n), json.loads(val)
        ru = src[n]
        if ph(ru) != ph(val):
            errs.append(f"{n}: плейсхолдеры {dict(ph(ru))} ≠ {dict(ph(val))}")
        if tags(ru) != tags(val):
            errs.append(f"{n}: теги {dict(tags(ru))} ≠ {dict(tags(val))}")
        out[ru] = val
    missing = [i for i, s in enumerate(src) if s not in out]
    if missing:
        errs.append(f"не переведены: {missing[:20]}{'…' if len(missing) > 20 else ''} ({len(missing)})")
    for e in errs:
        print(f"[{lang}] {e}")
    ok &= not errs
    (ROOT / "app" / "i18n" / f"{lang}.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"[{lang}] {len(out)}/{len(src)}")
sys.exit(0 if ok else 1)
