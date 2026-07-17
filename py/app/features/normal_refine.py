# -*- coding: utf-8 -*-
"""T4 일반재련 최적 정책 — 장인의 기운 100%까지 최소 비용 (순수 로직, Qt·네트워크 무관).

데이터는 loa-calc 이식(tools/extract_normal_refine.py), 엔진은 저장소 루트 refine.py 이식.
"""
import copy
import functools
import json
import math
import os
from dataclasses import dataclass

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "normal_refine.json")

with open(_DATA_PATH, encoding="utf-8") as _f:
    DATA = json.load(_f)

JANGIN_DIVIDER = DATA["jangin_divider"]        # 장인 증가량 = 성공확률 / 2.15
FAIL_STACK_RATIO = DATA["fail_stack_ratio"]    # 실패 1회 = +baseProb × 0.1
FAIL_STACK_CAP = DATA["fail_stack_cap"]        # 실패 스택 상한 = baseProb × 2

GOLD_KEY = "골드"
FRAGMENT_KEY = "운명파편"

# 여러 개를 쌓아 확률을 올리는 소모품. 이 목록 밖의 breath 항목(책)은 1개만 쓴다.
BREATH_NAMES = ("은총", "축복", "가호", "용암", "빙하")

_GOLD_REDUCTION_GRADE = "t4_1590"
_GOLD_REDUCTION_MAX_TARGET = 18
_GOLD_REDUCTION = 0.2


@dataclass(frozen=True)
class RefineTable:
    base_prob: float
    additional_prob: float
    jangin_multiplier: float
    amount: dict
    breath: dict


def grades(kind):
    return sorted(DATA["grades"][kind])


def targets(kind, grade):
    return sorted((int(t) for t in DATA["grades"][kind][grade]))


def stage_row(kind, grade, target):
    """DATA에 대한 방어적 깊은 복사본을 반환한다 — 호출자가 결과를 변형해도 전역 DATA는 오염되지 않는다."""
    return copy.deepcopy(DATA["grades"][kind][grade][str(target)])


def effective_amount(kind, grade, target):
    """골드 감면을 적용한 실제 소모량. t4_1590의 1–18단계만 20% 감면."""
    amount = dict(stage_row(kind, grade, target)["amount"])
    if grade == _GOLD_REDUCTION_GRADE and 1 <= target <= _GOLD_REDUCTION_MAX_TARGET:
        amount[GOLD_KEY] = math.ceil(amount[GOLD_KEY] * (1 - _GOLD_REDUCTION))
    return amount


_DEFAULT_BOOK = object()   # "일반 책이 있으면 그것" 을 뜻하는 센티널


def _plain_book(row):
    """이 단계의 일반 책 키. 없으면 None."""
    for name in row["breath"]:
        if name not in BREATH_NAMES:
            return name
    return None


def book_keys(kind, grade, target):
    """이 단계에서 쓸 수 있는 책 키 목록 [일반, 강화]. 책이 없으면 [None].

    책은 '특수 재료' 라 재련 1회당 하나만 쓸 수 있다. 따라서 이 목록의 원소는
    서로 배타적인 선택지이며, recommend() 는 각각으로 표를 만들어 최솟값을 취한다.
    """
    row = stage_row(kind, grade, target)
    keys = [name for name in (_plain_book(row),) if name is not None]
    keys += list(row.get("enhancedBook", {}))
    return keys or [None]


def material_keys(kind, grade, target):
    """값(시세)이 필요한 모든 재료 키. 골드·숨결·모든 책을 포함한다."""
    row = stage_row(kind, grade, target)
    keys = list(row["amount"])
    keys += [name for name in row["breath"] if name not in keys]
    keys += [name for name in row.get("enhancedBook", {}) if name not in keys]
    return keys


def build_table(kind, grade, target, book=_DEFAULT_BOOK):
    """책을 최대 한 권만 넣은 표. book 을 생략하면 일반 책(있으면)을 쓴다.

    일반 책과 강화 책을 한 breath 에 함께 넣으면 build_breath 의 누적 티어에
    '둘 다 사용' 이라는 불가능한 조합이 생긴다. 그래서 표를 나눈다.
    """
    row = stage_row(kind, grade, target)
    if book is _DEFAULT_BOOK:
        book = _plain_book(row)

    breath = {name: tuple(value) for name, value in row["breath"].items()
              if name in BREATH_NAMES}
    if book is not None:
        if book in row["breath"]:
            breath[book] = tuple(row["breath"][book])
        elif book in row.get("enhancedBook", {}):
            breath[book] = tuple(row["enhancedBook"][book])
        else:
            raise ValueError(f"{kind}/{grade}/{target}단계에 없는 책: {book}")

    return RefineTable(
        base_prob=row["baseProb"],
        additional_prob=0.0,
        jangin_multiplier=1.0,
        amount=effective_amount(kind, grade, target),
        breath=breath,
    )


def lookup_grade(kind, target, amounts):
    """(부위, 단계, 재료수량) → 등급. 아이템 이름은 등급을 가르지 못한다(결단·업화 모두 t4_1590).

    골드는 비교에서 완전히 제외한다 — 행에는 원본 골드가, 화면에는 감면된 골드가 찍히기 때문이다.
    나머지(비골드) 키를 모두 amounts가 제공하고 값이 일치해야 매칭으로 친다.
    여러 등급이 동시에 매칭되거나 하나도 매칭되지 않으면 판독 실패로 보고 None을 반환한다
    (부분/오독 OCR 입력이 확신에 찬 오답을 내지 않도록).
    """
    matches = []
    for grade in grades(kind):
        row = DATA["grades"][kind][grade].get(str(target))
        if not row:
            continue
        required = {k: v for k, v in row["amount"].items() if k != GOLD_KEY}
        if required and all(amounts.get(k) == v for k, v in required.items()):
            matches.append(grade)
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# 최적 정책 엔진 — 저장소 루트 refine.py 이식. 의미를 바꾸지 말 것.
# ---------------------------------------------------------------------------


def get_price(price_map, binded_map, amount_map):
    """소모량 중 보유(귀속)분을 뺀 나머지의 시세 합."""
    return sum(
        price_map[key] * max(amount - binded_map.get(key, 0), 0)
        for key, amount in amount_map.items()
    )


def subtract_amount(binded_map, amount_map):
    """보유량에서 이번 클릭 소모량을 뺀다. 0 이하가 된 항목은 버린다."""
    return {
        key: amount - amount_map.get(key, 0)
        for key, amount in binded_map.items()
        if amount - amount_map.get(key, 0) > 0
    }


def build_breath(price_map, breath_map, binded_map, base_prob):
    """숨결/책 조합을 '가성비 낮은 것부터' 누적한 티어 목록.

    반환 [i]는 상위 i종을 모두 쓴 경우다. [0]은 아무것도 쓰지 않음.
    BREATH_NAMES에 속하면 여러 개를 쌓고(확률을 base_prob까지만 채운다), 책은 1개만 쓴다.
    """
    breathes = sorted(
        breath_map.keys(),
        key=lambda name: (
            (max(breath_map[name][0] - binded_map.get(name, 0), 0) * price_map[name])
            / (breath_map[name][0] * breath_map[name][1]),
            price_map[name] / breath_map[name][1],
        ),
        reverse=True,
    )

    adjusted = {}
    prob_left = max(base_prob, 0.01)
    for name in breathes:
        breath_amount, breath_prob = breath_map[name]
        if name in BREATH_NAMES:
            amount = min(-(-prob_left // breath_prob), breath_amount)   # math.ceil
            adjusted[name] = {
                "price": max(amount - binded_map.get(name, 0), 0) * price_map[name],
                "prob": min(amount * breath_prob, prob_left),
                "amount": amount,
            }
            prob_left -= amount * breath_prob
        else:
            adjusted[name] = {
                "price": max(1 - binded_map.get(name, 0), 0) * price_map[name],
                "prob": breath_prob,
                "amount": 1,
            }

    result = [{"price": 0, "prob": 0, "breathes": {}}]
    for name in breathes:
        prev = result[-1]
        current = adjusted[name]
        result.append({
            "price": prev["price"] + current["price"],
            "prob": prev["prob"] + current["prob"],
            "breathes": {**prev["breathes"], name: current["amount"]},
        })
    return result


def optimize(table, price_map, binded_map, prob_from_failure, start_jangin):
    """장인 100%까지의 기대 골드를 최소화하는 정책. path[0]이 이번 클릭 조언."""
    base_prob = table.base_prob
    additional_prob = table.additional_prob
    default_base_price = get_price(price_map, {}, table.amount)
    default_breath = build_breath(price_map, table.breath, {}, base_prob)

    def rec(current_prob, jangin, global_prob, breath_count, binded_left):
        is_binded_empty = not binded_left
        base_price = (default_base_price if is_binded_empty
                      else get_price(price_map, binded_left, table.amount))
        breath = (default_breath if is_binded_empty
                  else build_breath(price_map, table.breath, binded_left, base_prob))

        if global_prob <= 0:
            return {"price": 0, "path": []}
        if jangin >= 1:
            return {"price": base_price,
                    "path": [{"base_prob": 1, "total_prob": 1, "global_prob": global_prob,
                              "jangin": 1, "price": base_price, "breathes": {}}]}

        prices = []
        paths = []
        for i in range(breath_count + 1):
            info = breath[i]
            prob = round(min(current_prob + additional_prob + info["prob"], 1), 4)
            fail = rec(
                min(current_prob + base_prob * FAIL_STACK_RATIO, base_prob * FAIL_STACK_CAP),
                jangin + (prob / JANGIN_DIVIDER) * table.jangin_multiplier,
                global_prob * (1 - prob),
                i,
                subtract_amount(subtract_amount(binded_left, table.amount), info["breathes"]),
            )
            prices.append(base_price + info["price"] + (1 - prob) * fail["price"])
            paths.append([{"base_prob": current_prob + additional_prob,
                           "total_prob": prob,
                           "global_prob": global_prob * prob,
                           "jangin": jangin,
                           "price": base_price + info["price"],
                           "breathes": info["breathes"]}, *fail["path"]])

        best = prices.index(min(prices))
        return {"price": prices[best], "path": paths[best]}

    return rec(base_prob + prob_from_failure, start_jangin, 1, len(table.breath), binded_map)


# ---------------------------------------------------------------------------
# 목표 (B): 장인 100% 충전 비용 + 조언 API
# ---------------------------------------------------------------------------

# 내부 재료 키 → 거래소 정식 아이템 이름. 부분 검색은 다른 아이템을 잡으므로 정확히 일치시킨다.
MARKET_NAMES = {
    "운명의수호석": "운명의 수호석",
    "운명의파괴석": "운명의 파괴석",
    "운명의수호석결정": "운명의 수호석 결정",
    "운명의파괴석결정": "운명의 파괴석 결정",
    "운돌": "운명의 돌파석",
    "위운돌": "위대한 운명의 돌파석",
    "아비도스": "아비도스 융화 재료",
    "상급아비도스": "상급 아비도스 융화 재료",
    "빙하": "빙하의 숨결",
    "용암": "용암의 숨결",
    "재봉술업화A": "재봉술 : 업화 [11-14]",
    "재봉술업화B": "재봉술 : 업화 [15-18]",
    "재봉술업화C": "재봉술 : 업화 [19-20]",
    "야금술업화A": "야금술 : 업화 [11-14]",
    "야금술업화B": "야금술 : 업화 [15-18]",
    "야금술업화C": "야금술 : 업화 [19-20]",
    "강화재봉술업화C": "강화 재봉술 : 업화 [19-20]",
    "강화야금술업화C": "강화 야금술 : 업화 [19-20]",
}

# 오류 메시지 표시용. 운명의 파편은 주머니로만 팔려 MARKET_NAMES(정확 검색용)에는 없다.
_DISPLAY_NAMES = {**MARKET_NAMES, FRAGMENT_KEY: "운명의 파편"}


def fill_optimize(table, price_map, binded_map, prob_from_failure, start_jangin):
    """장인 100% 를 '딱 맞게' 채우는 최저 비용 DP.

    매 클릭 어떤 소모품 티어든 고를 수 있다 (optimize 의 '비증가 티어' 제약은 쓰지 않는다 —
    그 제약은 기대 골드 목적에만 유효하고, 충전 목적에서는 최적을 배제한다: 평범하게 재련하다가
    정확히 100% 에 닿는 클릭에서만 숨결을 넣는 계획이 더 쌀 수 있다).

    optimize() 는 '성공까지 기대 골드'(확률 할인), 이 함수는 '장인을 다 채우는 비용'(할인 없음).
    마지막에 조금만 남으면 스스로 소모품을 끊는다 — 넘겨서 버리는 게 손해이기 때문이다.

    binded_map 은 첫 클릭 비용에만 반영한다. 클릭마다 보유량을 추적하면 상태가 폭발해
    캐시를 못 쓴다 — 이 함수는 '지금 뭘 넣을지' 를 알려주는 용도다.
    """
    base = table.base_prob
    additional = table.additional_prob
    base_price = get_price(price_map, binded_map, table.amount)
    breath = build_breath(price_map, table.breath, binded_map, base)

    @functools.lru_cache(maxsize=None)
    def rec(current_prob, jangin):
        if jangin >= 1.0 - 1e-12:
            return base_price, ()

        best = None
        for i in range(len(breath)):
            info = breath[i]
            prob = round(min(current_prob + additional + info["prob"], 1), 4)
            gain = (prob / JANGIN_DIVIDER) * table.jangin_multiplier
            next_prob = min(current_prob + base * FAIL_STACK_RATIO, base * FAIL_STACK_CAP)
            sub_price, sub_path = rec(round(next_prob, 10),
                                      min(round(jangin + gain, 10), 1.0))
            price = base_price + info["price"] + sub_price
            if best is None or price < best[0]:
                step = {"tier": i, "total_prob": prob, "jangin": jangin,
                        "price": base_price + info["price"],
                        "breathes": dict(info["breathes"])}
                best = (price, (step,) + sub_path)
        return best

    price, path = rec(round(base + prob_from_failure, 10), round(start_jangin, 10))
    terminal = {"tier": 0, "total_prob": 1, "jangin": 1.0,
                "price": base_price, "breathes": {}}
    return {"price": price, "path": list(path) + [terminal]}


def advice_text(breathes):
    """이번 클릭에 넣을 재료를 사람이 읽는 문장으로."""
    if not breathes:
        return "그냥 재련"
    return " + ".join(f"{MARKET_NAMES.get(name, name)} {int(count)}개"
                      for name, count in breathes.items())


def _missing_prices(table, price_map):
    return [key for key in list(table.amount) + list(table.breath)
            if key not in price_map]


def recommend(kind, grade, target, jangin_percent, current_prob_percent, price_map,
              binded_map=None):
    """이번 클릭 조언 + (A) 기대 골드 + (B) 장인 충전 비용.

    책은 '특수 재료' 라 재련 1회당 한 권만 쓸 수 있다. 그래서 책 종류마다 표를 따로 만들어
    각각 optimize 를 돌리고 가장 싼 변형을 고른다. 이렇게 하면 build_breath 의 누적 티어에
    '두 책 동시 사용' 이 생기지 않고, 검증된 이식 엔진을 한 줄도 건드리지 않는다.

    current_prob_percent: 실패 스택이 반영된 순수 성공률(%). None 이면 기본 확률.
    """
    binded = dict(binded_map or {})
    first_missing = None
    best = None

    for book in book_keys(kind, grade, target):
        table = build_table(kind, grade, target, book=book)

        missing = _missing_prices(table, price_map)
        if missing:
            if first_missing is None:
                first_missing = missing
            continue

        if current_prob_percent is None:
            prob_percent = table.base_prob * 100
        else:
            prob_percent = current_prob_percent
        prob_from_failure = min(max(prob_percent / 100.0 - table.base_prob, 0.0),
                                table.base_prob)
        start_jangin = min(max(jangin_percent / 100.0, 0.0), 1.0)

        result = optimize(table, price_map, binded, prob_from_failure, start_jangin)
        if best is not None and result["price"] >= best[0]["price"]:
            continue          # 동률이면 먼저 온 변형(일반 책)을 유지한다
        fill = fill_optimize(table, price_map, binded, prob_from_failure, start_jangin)["price"]
        best = (result, fill, book)

    if best is None:
        names = ", ".join(_DISPLAY_NAMES.get(k, k) for k in first_missing)
        raise ValueError("시세 없음: " + names)

    result, fill, book = best
    head = result["path"][0]
    return {
        "expected_gold": result["price"],
        "fill_gold": fill,
        "breathes": head["breathes"],
        "total_prob": head["total_prob"],
        "advice": advice_text(head["breathes"]),
        "book": book,
        "path": result["path"],
    }


COMBOS = ("none", "breath", "book", "both")

_COMBO_LABEL = {"none": "그냥 재련", "breath": "숨결만", "book": "책만", "both": "숨결 + 책"}


def _breath_name(row):
    """이 단계의 숨결 이름 (빙하 또는 용암). 없으면 None."""
    for name in row["breath"]:
        if name in BREATH_NAMES:
            return name
    return None


def combo_table(kind, grade, target, combo, book=None):
    """소모품 조합 하나만 담은 표. 그 조합이 불가능하면 None.

    build_breath 는 누적 티어 [없음, 가성비 나쁜 것, 둘 다] 만 만들어서, 한 표 안에서는
    '가성비 좋은 것 하나만' 이 표현되지 않는다. 그래서 조합마다 표를 나눈다.
    """
    row = stage_row(kind, grade, target)
    breath_name = _breath_name(row)
    if book is None and combo in ("book", "both"):
        keys = [k for k in book_keys(kind, grade, target) if k is not None]
        if not keys:
            return None
        book = keys[0]

    entries = {}
    if combo in ("breath", "both"):
        if breath_name is None:
            return None
        entries[breath_name] = tuple(row["breath"][breath_name])
    if combo in ("book", "both"):
        if book in row["breath"]:
            entries[book] = tuple(row["breath"][book])
        elif book in row.get("enhancedBook", {}):
            entries[book] = tuple(row["enhancedBook"][book])
        else:
            return None

    return RefineTable(base_prob=row["baseProb"], additional_prob=0.0,
                       jangin_multiplier=1.0,
                       amount=effective_amount(kind, grade, target),
                       breath=entries)


def _candidates(kind, grade, target):
    """(combo, book) 후보 전부. 책이 없는 단계면 none/breath 만 나온다."""
    books = [b for b in book_keys(kind, grade, target) if b is not None]
    out = [("none", None), ("breath", None)]
    for book in books:
        out.append(("book", book))
        out.append(("both", book))
    return out


def recommend_fill(kind, grade, target, jangin_percent, current_prob_percent, price_map,
                   binded_map=None):
    """장인 100% 를 가장 싸게 채우는 조합과 이번 클릭 조언.

    주 목표는 fill_gold (장인 충전 최저비용). expected_gold 는 참고값이다.
    current_prob_percent: 실패 스택이 반영된 순수 성공률(%). None 이면 기본 확률.
    """
    binded = dict(binded_map or {})
    base_table = build_table(kind, grade, target)

    missing = [k for k in list(base_table.amount) if k not in price_map]
    if missing:
        raise ValueError("시세 없음: " + ", ".join(_DISPLAY_NAMES.get(k, k) for k in missing))

    if current_prob_percent is None:
        current_prob_percent = base_table.base_prob * 100
    prob_from_failure = min(max(current_prob_percent / 100.0 - base_table.base_prob, 0.0),
                            base_table.base_prob)
    start_jangin = min(max(jangin_percent / 100.0, 0.0), 1.0)

    best = None
    for combo, book in _candidates(kind, grade, target):
        table = combo_table(kind, grade, target, combo, book=book)
        if table is None:
            continue
        if any(name not in price_map for name in table.breath):
            continue    # 그 소모품 시세가 없으면 그 조합만 건너뛴다
        result = fill_optimize(table, price_map, binded, prob_from_failure, start_jangin)
        if best is None or result["price"] < best[0]["price"]:
            best = (result, combo, book, table)

    if best is None:
        raise ValueError("시세 없음: 조언할 수 있는 조합이 없습니다")

    result, combo, book, table = best
    head = result["path"][0]
    expected = optimize(base_table, price_map, binded, prob_from_failure, start_jangin) \
        if all(n in price_map for n in base_table.breath) else {"price": float("nan")}

    return {
        "fill_gold": result["price"],
        "expected_gold": expected["price"],
        "combo": combo,
        "combo_label": _COMBO_LABEL[combo],
        "book": book,
        "breathes": head["breathes"],
        "total_prob": head["total_prob"],
        "advice": advice_text(head["breathes"]),
        "path": result["path"],
    }


def fetch_prices(client, kind, grade, target):
    """이 단계에 실제로 쓰이는 재료만 거래소에서 개당가로 조회. 골드는 1:1.

    강화 책까지 포함한다 (material_keys). 묶음 보정과 파편 주머니 환산은 상급재련에서
    검증된 헬퍼를 그대로 쓴다. 조회 실패 항목은 생략하고, recommend() 가 처리한다.
    """
    from app.features.advanced_refine import _fragment_unit_price, _unit_price

    prices = {GOLD_KEY: 1}
    for key in material_keys(kind, grade, target):
        if key == GOLD_KEY or key in prices:
            continue
        price = (_fragment_unit_price(client) if key == FRAGMENT_KEY
                 else _unit_price(client, MARKET_NAMES[key]))
        if price is not None:
            prices[key] = price
    return prices
