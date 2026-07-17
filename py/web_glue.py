# -*- coding: utf-8 -*-
"""웹 계산기 글루 — py-runtime.js 가 호출하는 JSON 친화 진입점.

엔진(app/features/*)은 데스크탑 앱 원본 그대로다. 이 모듈은
- 백엔드 /api/prices/refine 원시 시세(한국어 아이템명)를 엔진 입력 키로 매핑
  (앱 price_cache.normal_prices()/advanced_prices() 와 동형 — 로직 수정 시 함께 반영)
- dataclass·tuple·inf 등 JSON 으로 못 건너가는 값을 정리
만 담당한다. 계산 로직을 여기 두지 말 것.
"""
import math

import app.features.advanced_refine as ar
import app.features.crit_rate as cr
import app.features.gem_craft as gc
import app.features.normal_refine as nr

# ---------- 시세 매핑 ----------

def _normal_price_map(raw):
    prices = {nr.GOLD_KEY: 1}
    for key, name in nr.MARKET_NAMES.items():
        price = raw.get(name)
        if price is not None:
            prices[key] = price
    fragment = raw.get("__fragment__")
    if fragment is not None:
        prices[nr.FRAGMENT_KEY] = fragment
    return prices


def _advanced_price_map(raw, equip, band):
    info = ar.band_info(equip, band)
    fragment_item = ar.DATA["fragment_item"]
    prices = {}
    for name in info["amount"]:
        if name == "골드":
            continue
        price = raw.get("__fragment__") if name == fragment_item else raw.get(name)
        if price is not None:
            prices[name] = price
    for name in (info["breath"]["name"], info["book"]):
        price = raw.get(name)
        if price is not None:
            prices[name] = price
    return prices


def _json_safe(value):
    """inf/nan → None, numpy 스칼라 → 파이썬 수 (JSON 직렬화 안전화)."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return _json_safe(value.item())
    return value


# ---------- 폼 옵션 (페이지 셸이 select 를 채울 때 1회 호출) ----------

_NORMAL_GRADE_LABELS = {"t4_1590": "1590 (전율)", "t4_1730": "1730 (업화)"}
_BAND_LABELS = [("t4_0", "0–10단계"), ("t4_1", "10–20단계"),
                ("t4_2", "20–30단계"), ("t4_3", "30–40단계")]
_KIND_LABELS = [("weapon", "무기"), ("armor", "방어구")]


def options():
    kinds = [k for k, _ in _KIND_LABELS]
    return {
        "normal": {
            "kinds": [{"value": k, "label": l} for k, l in _KIND_LABELS],
            "grades": {
                k: [{"value": g, "label": _NORMAL_GRADE_LABELS.get(g, g)}
                    for g in nr.grades(k)]
                for k in kinds
            },
            "targets": {f"{k}:{g}": nr.targets(k, g)
                        for k in kinds for g in nr.grades(k)},
        },
        "advanced": {
            "equips": [{"value": k, "label": l} for k, l in _KIND_LABELS],
            "bands": [{"value": v, "label": l} for v, l in _BAND_LABELS],
            "stacks": [0, 2, 4, 6],
            "max_exp": ar.MAX_EXP,
        },
        "gem": {
            "grades": [{"value": g, "label": g,
                        "attempts": info["attempts"], "rerolls": info["rerolls"]}
                       for g, info in gc.GRADES.items()],
            "targets": [{"index": i, "label": label}
                        for i, (label, _t) in enumerate(gc.STANDARD_TARGETS)],
            "default_target": gc.DEFAULT_TARGET_INDEX,
            "stat_max": gc.STAT_MAX,
            "entries": [{"id": e["id"], "label": gc.entry_label(e["id"])}
                        for e in gc.DATA["pool"]],
        },
    }


# ---------- 일반재련 ----------

def normal_calc(kind, grade, target, jangin_percent, current_prob_percent, raw_prices):
    price_map = _normal_price_map(raw_prices)
    try:
        rec = nr.recommend_fill(kind, grade, target, jangin_percent,
                                current_prob_percent, price_map)
    except ValueError as exc:
        return {"error": str(exc)}
    return _json_safe({
        "combo": rec["combo"],
        "combo_label": rec["combo_label"],
        "book": rec["book"],
        "advice": rec["advice"],
        "breathes": rec["breathes"],
        "total_prob": rec["total_prob"],
        "fill_gold": rec["fill_gold"],
        "expected_gold": rec["expected_gold"],
    })


# ---------- 상급재련 ----------

def advanced_calc(equip, band, growth_support, exp, stack, free_next, enh_next,
                  raw_prices):
    prices = _advanced_price_map(raw_prices, equip, band)
    rec = ar.recommend_state(equip, band, prices, growth_support,
                             exp, stack, free_next, enh_next)
    parts = []
    if rec["breath"]:
        parts.append("숨결 사용")
    if rec["book"]:
        parts.append("책 사용")
    info = ar.band_info(equip, band)
    return _json_safe({
        "breath": rec["breath"],
        "book": rec["book"],
        "action_label": " + ".join(parts) if parts else "아무것도 넣지 말고 재련",
        "expected_gold": rec["expected_gold"],
        "band_expected_gold": rec["band_expected_gold"],
        "options": [{"breath": b, "book": k, "expected_gold": v}
                    for (b, k), v in rec["options"]],
        "breath_name": info["breath"]["name"],
        "book_name": info["book"],
        "missing_prices": [n for n in (info["breath"]["name"], info["book"])
                           if n not in prices],
    })


# ---------- 젬파고 ----------

_GEM_ENGINE = None


def _gem_engine():
    global _GEM_ENGINE
    if _GEM_ENGINE is None:
        _GEM_ENGINE = gc.GemCraftEngine()
    return _GEM_ENGINE


def gem_calc(grade, willpower, points, effect1, effect2, attempts, rerolls,
             resets_left, target_index, shown_ids=None):
    state = gc.GemState(willpower, points, effect1, effect2, attempts, rerolls)
    engine = _gem_engine()

    targets = [{
        "label": label,
        "probability": engine.success_probability(
            state, target, resets_left=resets_left, grade=grade),
        "satisfied": target.satisfied(state),
    } for label, target in gc.STANDARD_TARGETS]

    target = gc.STANDARD_TARGETS[target_index][1]
    rec = engine.recommend_action(state, target, shown_ids=shown_ids or None,
                                  grade=grade, resets_left=resets_left)
    return _json_safe({
        "targets": targets,
        "target_label": gc.STANDARD_TARGETS[target_index][0],
        "recommend": rec,
        "available_entries": [{"id": entry["id"], "label": gc.entry_label(entry["id"]),
                               "prob": prob}
                              for entry, prob in gc.available_entries(state)],
    })


# ---------- 치명타 ----------

def crit_calc(armory, back_attack=False, crit_pet=False):
    return _json_safe(cr.calculate(armory, back_attack=back_attack,
                                   crit_pet=crit_pet))


# ---------- 화면 인식 (Pyodide opencv — py-runtime bootVision() 후에만 호출) ----------
#
# 판독기는 데스크탑 앱과 동일한 app/vision/* 원본이다. 프레임은 JS 캔버스의
# RGBA 바이트로 받아 BGR 로 변환해 넘긴다. 판독기별 앵커 힌트를 모듈 상태로
# 유지해 연속 폴링을 빠르게 한다 (데스크탑과 같은 최적화).

_VISION = {}


def _frame_bgr(frame_bytes, width, height):
    import cv2
    import numpy as np
    buf = frame_bytes.to_py() if hasattr(frame_bytes, "to_py") else frame_bytes
    arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(int(height), int(width), 4)
    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)


def _vision_slot(name):
    slot = _VISION.get(name)
    if slot is None:
        if name == "gem":
            from app.vision.gem_window import GemWindowReader
            # unknown_dir 지정으로 데스크탑 전용 app.config 의존을 우회
            slot = {"reader": GemWindowReader(unknown_dir="/tmp/vision_unknown")}
        elif name == "advanced":
            from app.vision.refine_window import RefineWindowReader
            slot = {"reader": RefineWindowReader()}
        else:
            from app.vision.normal_refine_confirm import ConfirmWindowReader
            slot = {"reader": ConfirmWindowReader()}
        slot["hint"] = None
        _VISION[name] = slot
    return slot


def vision_gem(frame, width, height, resets_left=None, target_index=None):
    slot = _vision_slot("gem")
    reading = slot["reader"].read(_frame_bgr(frame, width, height),
                                  hint=slot["hint"])
    if reading is None:
        slot["hint"] = None
        return {"found": False}
    slot["hint"] = reading.anchor

    info = {
        "found": True,
        "complete": bool(reading.complete()),
        "grade": reading.grade(),
        "willpower": reading.willpower, "points": reading.points,
        "effect1": reading.effect1, "effect2": reading.effect2,
        "attempts": reading.attempts, "rerolls": reading.rerolls,
        "resets": reading.resets,
        "shown_ids": list(reading.shown_ids),
        "shown_labels": list(reading.shown_labels),
        "warnings": list(reading.warnings),
    }
    if info["complete"] and info["grade"]:
        info["result"] = gem_calc(
            info["grade"], reading.willpower, reading.points,
            reading.effect1, reading.effect2, reading.attempts, reading.rerolls,
            reading.resets if reading.resets is not None else (resets_left or 0),
            gc.DEFAULT_TARGET_INDEX if target_index is None else int(target_index),
            [e for e in reading.shown_ids if e] or None)
    return _json_safe(info)


def vision_advanced(frame, width, height, raw_prices, growth_support=False,
                    free_next=False):
    slot = _vision_slot("advanced")
    reading = slot["reader"].read(_frame_bgr(frame, width, height),
                                  hint=slot["hint"])
    if reading is None:
        slot["hint"] = None
        return {"found": False}
    slot["hint"] = reading.anchor

    info = {
        "found": True,
        "complete": bool(reading.complete()),
        "level": reading.level, "exp": reading.exp, "band": reading.band,
        "stack": reading.stack, "enh_next": bool(reading.enh_next),
        "equip": reading.equip_type,
        "warnings": list(reading.warnings),
    }
    if info["complete"]:
        info["result"] = advanced_calc(
            reading.equip_type, reading.band, growth_support,
            reading.exp, reading.stack, free_next, reading.enh_next, raw_prices)
    return _json_safe(info)


def vision_normal(frame, width, height, raw_prices, default_grade=None):
    from app.features import normal_refine_fusion as fusion
    slot = _vision_slot("normal")
    reading = slot["reader"].read(_frame_bgr(frame, width, height),
                                  hint=slot["hint"])
    if reading is None:
        slot["hint"] = None
        return {"found": False}
    slot["hint"] = (reading.anchor, reading.scale)

    info = {
        "found": True,
        "complete": bool(reading.complete()),
        "kind": reading.kind, "level": reading.level, "target": reading.target,
        "jangin": reading.jangin, "prob_current": reading.prob_current,
        "gear_grade": reading.gear_grade,
        "warnings": list(reading.warnings),
    }
    if info["complete"]:
        try:
            result = fusion.advise(reading, None, _normal_price_map(raw_prices),
                                   default_grade=default_grade)
            info["result"] = {
                "combo": result["combo"], "combo_label": result["combo_label"],
                "book": result.get("book"), "advice": result["advice"],
                "breathes": result["breathes"], "total_prob": result["total_prob"],
                "fill_gold": result["fill_gold"],
                "expected_gold": result.get("expected_gold"),
                "grade": result.get("grade"),
                "grade_source": result.get("grade_source"),
                "jangin": reading.jangin,
            }
        except fusion.NeedMainWindow as exc:
            info["error"] = str(exc)
        except ValueError as exc:
            info["error"] = str(exc)
    return _json_safe(info)
