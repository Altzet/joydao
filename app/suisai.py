"""Движок расчётов Сюцай.

Логика повторяет первоисточник (suisai-pro):
- Число Сознания (ЧС) — свёртка дня рождения к одной цифре;
- Миссия — свёртка всех цифр даты рождения;
- Личный Год — свёртка (день + месяц + текущий год);
- Матрица — количество каждой энергии 1..9 в цифрах даты рождения;
- Число имени — латинские буквы по таблице соответствий.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

with open(DATA_DIR / "suisai_data.json", encoding="utf-8") as f:
    TEXTS: dict[str, list[str]] = json.load(f)


def _clean_text(raw: str) -> str:
    """Убирает «лесенку» отступов первоисточника и лишние пустые строки."""
    lines = [line.strip() for line in raw.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()

# Таблица «буква → цифра» из первоисточника
_LETTER_MAP: dict[str, int] = {}
for _digit, _letters in [(1, "1aijqy"), (2, "2bkr"), (3, "3clsg"), (4, "4dmt"),
                         (5, "5ehnx"), (6, "6uvw"), (7, "7oz"), (8, "8fp"), (9, "9")]:
    for _ch in _letters:
        _LETTER_MAP[_ch] = _digit


def reduce_number(n: int) -> int:
    """Свёртка числа к одной цифре (нумерологическое сложение)."""
    while n > 9:
        n = sum(int(d) for d in str(n))
    return n


def consciousness_number(birth: date) -> int:
    """Число Сознания — по дню рождения."""
    return reduce_number(birth.day)


def mission_number(birth: date) -> int:
    """Миссия — сумма всех цифр даты рождения."""
    digits = f"{birth.day}{birth.month}{birth.year}"
    return reduce_number(sum(int(d) for d in digits))


def personal_year(birth: date, today: date | None = None) -> int:
    """Личный Год — день + месяц рождения + текущий год."""
    today = today or date.today()
    digits = f"{birth.day}{birth.month}{today.year}"
    return reduce_number(sum(int(d) for d in digits))


def matrix(birth: date) -> dict[int, int]:
    """Матрица: сколько раз каждая энергия 1..9 встречается в дате рождения."""
    digits = f"{birth.day:02d}{birth.month:02d}{birth.year}"
    return {e: digits.count(str(e)) for e in range(1, 10)}


def missing_energies(birth: date) -> list[int]:
    """«Недостающие компетенции» — пустые энергии матрицы."""
    return [e for e, cnt in matrix(birth).items() if cnt == 0]


def energy_groups(d: date) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Баланс даты: (усиленные [(энергия, раз)], встречаются один раз, отсутствуют). Нули не считаются."""
    m = matrix(d)
    return ([(e, c) for e, c in m.items() if c >= 2],
            [e for e, c in m.items() if c == 1],
            [e for e, c in m.items() if c == 0])


def name_number(name_latin: str) -> int:
    """Число имени (латиницей, как в загранпаспорте)."""
    total = sum(_LETTER_MAP.get(ch, 0) for ch in name_latin.lower())
    return reduce_number(total) if total else 0


def portrait(birth: date, today: date | None = None) -> dict:
    """Полный портрет Сюцай с текстами первоисточника."""
    chs = consciousness_number(birth)
    mission = mission_number(birth)
    year = personal_year(birth, today)
    return {
        "chs": chs,
        "mission": mission,
        "personal_year": year,
        "matrix": matrix(birth),
        "matrix_layout": MATRIX_LAYOUT,
        "missing": missing_energies(birth),
        "texts": {
            "chs": _clean_text(TEXTS["chsDescription"][chs - 1]),
            "triggers": _clean_text(TEXTS["triggers"][chs - 1]),
            "mission": _clean_text(TEXTS["missionDescription"][mission - 1]),
            "task": _clean_text(TEXTS["taskDescription"][chs - 1]),
            "year": _clean_text(TEXTS["yearDescription"][year - 1]),
        },
    }


# Краткие бережные формулировки для карточек (адаптация для продукта)
CHS_SHORT = {
    1: "Лидер и стратег: сила Солнца, самостоятельность, покровительство",
    2: "Дипломат: чуткость Луны, мягкость, умение чувствовать людей",
    3: "Учитель: энергия Юпитера, знания, оптимизм и щедрость",
    4: "Новатор: энергия Раху, нестандартное мышление, свой путь",
    5: "Коммуникатор: живость Меркурия, лёгкость, гибкий ум",
    6: "Гармонизатор: энергия Венеры, любовь, красота, забота",
    7: "Исследователь: глубина Кету, интуиция, тонкое восприятие",
    8: "Труженик: сила Сатурна, дисциплина, надёжность, результат",
    9: "Воин: энергия Марса, воля, страсть, защита близких",
}

ENERGY_NAMES = {
    1: "власть", 2: "творческая психология", 3: "анализ",
    4: "передача знаний", 5: "борьба", 6: "творчество",
    7: "йога", 8: "упорный труд", 9: "служение людям",
}

# Раскладка матрицы «квадрат Пифагора» — порядок ячеек для отображения
# (ряды сверху вниз, слева направо): 3 6 9 / 2 5 8 / 1 4 7
MATRIX_LAYOUT = [3, 6, 9, 2, 5, 8, 1, 4, 7]


# Значения итоговой цифры для расчёта имени/города/карты/телефона (первоисточник)
TEXT_CALC_MEANINGS = {
    1: "Популярность",
    2: "Личное благополучие, к замужеству",
    3: "Солидность",
    4: "Отчуждённость",
    5: "Артистизм",
    6: "Успех, к замужеству",
    7: "Вызов природе",
    8: "Постоянный упорный труд",
    9: "Эмоциональная глубина",
}


def personal_month(birth: date, today: date | None = None) -> int:
    """Личный Месяц — свёртка (личный год + текущий месяц), как в первоисточнике."""
    today = today or date.today()
    return reduce_number(personal_year(birth, today) + today.month)


def personal_day(birth: date, today: date | None = None) -> int:
    """Личный День — свёртка (личный месяц + число дня). Основа «энергии дня»."""
    today = today or date.today()
    return reduce_number(personal_month(birth, today) + today.day)
