"""Миссия набирает вес после 33 лет."""
from datetime import date
from app import compat

TODAY = date(2026, 10, 14)


def test_phase_bounds():
    assert compat.mission_phase(date(2000, 1, 1), TODAY) == 0       # 26 лет — только ЧС
    assert compat.mission_phase(date(1980, 1, 1), TODAY) == 1       # 46 лет — миссия в силе
    assert 0 < compat.mission_phase(date(1989, 1, 1), TODAY) < 1    # 37 лет — переход


def test_weights_keep_total_and_shift_to_mission():
    w = compat.RULES["weights"]
    young, mature = compat._age_weights(w, 0), compat._age_weights(w, 1)
    assert abs(sum(young.values()) - sum(w.values())) < 1e-9
    assert young["chs_harmony"] > young["mission_resonance"]
    assert mature["mission_resonance"] > mature["chs_harmony"]


def test_result_has_phase():
    r = compat.compatibility(date(1955, 2, 24), date(1950, 8, 11), TODAY, kind="business")
    assert r["mission_phase"] == 1 and 0 <= r["score"] <= 100
