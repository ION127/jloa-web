"""상급재련 최적 정책 계산 (순수 로직, Qt 무관).

loa-calc advanced-refining 메커니즘 이식. 매 클릭 최적 행동(숨결·책 사용 여부)을
역산하는 메모이즈 DP. 확률·보상은 인게임 툴팁(2026-07-09)과 일치.
"""
import json
import math
import os
import random

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                          "advanced_refine.json")

with open(_DATA_PATH, encoding="utf-8") as _f:
    DATA = json.load(_f)

SUCCESS_TABLE = {int(k): v for k, v in DATA["success_table"].items()}
EXP_GAIN = DATA["exp_gain"]            # [10, 20, 40]
MAX_EXP = DATA["max_exp"]             # 1000


def band_info(equip_type: str, band: str) -> dict:
    return DATA["bands"][equip_type][band]


# ---------- 보상 상태전이 (simulation.ts getBonus/getEnhancedBonus 이식) ----------

def apply_reward(reward, exp, normal_exp, enhanced, threshold):
    """보너스 보상 적용 → (exp, stack, free_next, enh_next). enhanced=강화표 여부.

    normal_exp = 이 보너스 클릭의 exp 증가분(10/20/40, 성공/대성공/대성공x2).
    """
    if reward in ("갈라투르", "겔라르"):
        mult = ({"갈라투르": 7, "겔라르": 5} if enhanced
                else {"갈라투르": 5, "겔라르": 3})[reward]
        return exp + normal_exp * mult, 0, False, False
    if reward == "쿠훔바르":
        add = 80 if enhanced else 30
        return exp + normal_exp + add, threshold, False, False   # 재충전(완충 유지)
    if reward == "테메르":
        add = 30 if enhanced else 10
        return exp + normal_exp + add, 0, True, False            # 다음 재련 무료
    if reward == "나베르":  # 강화표엔 없음
        return exp + normal_exp, threshold, False, True          # 다음 보너스 강화
    # 에베르: 다음 단계로 올림(강화면 2단계)
    step = 200 if enhanced else 100
    return (exp + normal_exp) // 100 * 100 + step, 0, False, False


def _draw_exp(rng, proc):
    r = rng.random()
    if r < proc[0]:
        return EXP_GAIN[0]
    if r < proc[0] + proc[1]:
        return EXP_GAIN[1]
    return EXP_GAIN[2]


def _pick(rng, table):
    r = rng.random()
    cum = 0.0
    name = None
    for name, p in table.items():
        cum += p
        if r < cum:
            return name
    return name  # 부동소수 잔차


def simulate(bonus_table, has_enh, normal_k, bonus_k, enh_k, inc, thr,
             iters=200000, seed=12345):
    """고정 전략(클릭 유형별 K 고정)으로 밴드 완주 try 수 몬테카를로 (simulation.ts run 이식).

    has_enh: 밴드에 강화보너스가 존재하는지(참고용; 실제 강화 발동은 나베르→enh_next).
    반환: 평균 {freeNormalTry, paidNormalTry, bonusTry, enhancedBonusTry}.
    """
    rng = random.Random(seed)
    enh_table = DATA["enhanced_bonus_table"]
    totals = {"freeNormalTry": 0, "paidNormalTry": 0, "bonusTry": 0,
              "enhancedBonusTry": 0}
    nproc, bproc, eproc = SUCCESS_TABLE[normal_k], SUCCESS_TABLE[bonus_k], SUCCESS_TABLE[enh_k]
    for _ in range(iters):
        exp = stack = 0
        free_next = enh_next = False
        while exp < MAX_EXP:
            if stack >= thr:
                if enh_next:
                    totals["enhancedBonusTry"] += 1
                    ne = _draw_exp(rng, eproc)
                    exp, stack, free_next, enh_next = apply_reward(
                        _pick(rng, enh_table), exp, ne, True, thr)
                else:
                    totals["bonusTry"] += 1
                    ne = _draw_exp(rng, bproc)
                    exp, stack, free_next, enh_next = apply_reward(
                        _pick(rng, bonus_table), exp, ne, False, thr)
            else:
                if free_next:
                    free_next = False
                    totals["freeNormalTry"] += 1
                else:
                    totals["paidNormalTry"] += 1
                exp += _draw_exp(rng, nproc)
                stack += inc
    return {k: v / iters for k, v in totals.items()}


# ---------- 최적 정책 DP ----------

class RefineEngine:
    """매 클릭 최적 행동(숨결·책)을 역산하는 메모이즈 DP.

    value(state) = min_행동[ 이번 클릭 비용 + Σ P·value(next) ], 종료 value(exp>=MAX)=0.
    """

    def __init__(self, base_cost, breath_cost, book_cost, bonus_table,
                 has_enh, inc, thr):
        self.base = float(base_cost)
        self.breath = float(breath_cost)
        self.book = float(book_cost)
        self.bonus = bonus_table
        self.enh = DATA["enhanced_bonus_table"]
        self.has_enh = has_enh
        self.inc = inc
        self.thr = thr
        self._memo = {}

    def _action_cost(self, breath, book, free):
        return ((0.0 if free else self.base)
                + (self.breath if breath else 0.0)
                + (self.book if book else 0.0))

    def _normal_value(self, exp, stack, free_next, breath, book):
        k = (1 if breath else 0) + (2 if book else 0)
        proc = SUCCESS_TABLE[k]
        ev = self._action_cost(breath, book, free_next)
        nstack = stack + self.inc
        for gain, p in zip(EXP_GAIN, proc):
            if p <= 0:
                continue
            nexp = exp + gain
            child = 0.0 if nexp >= MAX_EXP else self.value(nexp, nstack, False, False)
            ev += p * child
        return ev

    def _bonus_value(self, exp, breath, book, enh_next):
        k = (1 if breath else 0) + (2 if book else 0)
        proc = SUCCESS_TABLE[k]
        table = self.enh if enh_next else self.bonus
        ev = self._action_cost(breath, book, False)   # 보너스 클릭은 유료
        for gain, pg in zip(EXP_GAIN, proc):
            if pg <= 0:
                continue
            for reward, pr in table.items():
                if pr <= 0:
                    continue
                nexp, nstack, nfree, nenh = apply_reward(reward, exp, gain, enh_next, self.thr)
                child = 0.0 if nexp >= MAX_EXP else self.value(nexp, nstack, nfree, nenh)
                ev += pg * pr * child
        return ev

    def _eval_actions(self, exp, stack, free_next, enh_next):
        forced_bonus = stack >= self.thr
        out = []
        for breath in (False, True):
            for book in (False, True):
                v = (self._bonus_value(exp, breath, book, enh_next) if forced_bonus
                     else self._normal_value(exp, stack, free_next, breath, book))
                out.append(((breath, book), v))
        return out

    def value(self, exp, stack, free_next, enh_next):
        if exp >= MAX_EXP:
            return 0.0
        key = (exp, stack, free_next, enh_next)
        cached = self._memo.get(key)
        if cached is not None:
            return cached
        best = min(v for _, v in self._eval_actions(exp, stack, free_next, enh_next))
        self._memo[key] = best
        return best

    def recommend(self, exp, stack, free_next, enh_next):
        options = self._eval_actions(exp, stack, free_next, enh_next)
        (breath, book), best = min(options, key=lambda x: x[1])
        return {"breath": breath, "book": book,
                "expected_gold": best, "options": options}


# ---------- 비용 모델 · 시세 연동 · 추천 진입점 ----------

def compute_costs(equip_type, band, prices, growth_support):
    """(base_cost, breath_cost, book_cost). base=기본 재료비+골드(유료 클릭 1회분)."""
    info = band_info(equip_type, band)
    red = DATA["reduction"]
    reduce = growth_support and info.get("reducible", False)

    def eff(amount, kind):
        if not reduce:
            return amount
        factor = {"gold": red["gold"], "fragment": red["fragment"]}.get(kind, red["cost"])
        return math.ceil(amount * (1 - factor))

    base = 0.0
    for mat, amt in info["amount"].items():
        if mat == "골드":
            base += eff(amt, "gold")                       # 골드는 1:1
        elif "파편" in mat:
            base += eff(amt, "fragment") * prices.get(mat, 0)
        else:
            base += eff(amt, "cost") * prices.get(mat, 0)
    breath_amt = eff(info["breath"]["per_use"], "cost")
    # 시세를 못 받은 추가재료(숨결/책)는 '공짜'가 아니라 '가격 미상'이다. 0으로 두면
    # 엔진이 공짜로 여겨 항상 추천해버려(실사용 버그) loa-calc('책만')와 어긋난다.
    # 미상은 무한대 비용으로 둬 추천 대상에서 제외한다 — 시세가 다 있으면 loa-calc와 일치.
    breath_name = info["breath"]["name"]
    breath = breath_amt * prices[breath_name] if breath_name in prices else float("inf")
    book = prices[info["book"]] if info["book"] in prices else float("inf")
    return float(base), float(breath), float(book)


def make_engine(equip_type, band, prices, growth_support, use_loacalc_fill=False):
    info = band_info(equip_type, band)
    base, breath, book = compute_costs(equip_type, band, prices, growth_support)
    inc = DATA["loacalc_fill_increment"] if use_loacalc_fill else DATA["fill_increment"]
    thr = DATA["loacalc_fill_threshold"] if use_loacalc_fill else DATA["fill_threshold"]
    return RefineEngine(base, breath, book, DATA["bonus_tables"][info["bonus_table"]],
                        info["has_enhanced_bonus"], inc, thr)


def recommend_state(equip_type, band, prices, growth_support,
                    exp, stack, free_next, enh_next):
    eng = make_engine(equip_type, band, prices, growth_support)
    rec = eng.recommend(exp, stack, free_next, enh_next)
    rec["band_expected_gold"] = eng.value(0, 0, False, False)
    return rec


def material_names(equip_type, band):
    """표시용 재료 이름 목록 (골드 제외). 파편은 개당 환산 대상."""
    info = band_info(equip_type, band)
    names = [m for m in info["amount"] if m != "골드"]
    names.append(info["breath"]["name"])
    names.append(info["book"])
    return names


REFINE_CATEGORY = 50000   # 거래소 '강화 재료' 카테고리 (재련 재료 전부 여기 있음)


def _search_refine(client, name):
    """강화 재료 카테고리에서만 이름 검색 → (최저가, 묶음수) 또는 None.

    search_market_items는 13개 전체 카테고리로 부채질(fan-out)해 조회 8종이면 100회+
    호출로 레이트리밋에 걸린다. 재련 재료는 전부 50000이라 단일 카테고리만 친다.
    """
    try:
        data = client.post("/markets/items", {
            "Sort": "CURRENT_MIN_PRICE", "CategoryCode": REFINE_CATEGORY,
            "ItemName": name, "PageNo": 0, "SortCondition": "ASC"}, retries=1)
    except Exception:
        return None
    items = data.get("Items") if isinstance(data, dict) else data
    for it in items or []:
        if it.get("Name") == name and it.get("CurrentMinPrice") is not None:
            return it["CurrentMinPrice"], (it.get("BundleCount") or 1)
    return None


def _unit_price(client, name):
    """거래소 최저가를 '개당' 가격으로 환산 (묶음 BundleCount로 나눔). 없으면 None."""
    hit = _search_refine(client, name)
    if hit is None:
        return None
    price, bundle = hit
    return price / bundle


def _fragment_unit_price(client):
    """운명의 파편 개당가 = 주머니(소/중/대) 중 개당 최저. 없으면 None."""
    best = None
    for bag, count in DATA["fragment_bags"].items():
        hit = _search_refine(client, bag)
        if hit is None:
            continue
        per = hit[0] / count
        if best is None or per < best:
            best = per
    return best


def fetch_prices(client, equip_type, band):
    """재료별 '개당' 시세(묶음 보정). 파편은 주머니 개당가로 환산. 실패 항목은 생략."""
    info = band_info(equip_type, band)
    frag = DATA["fragment_item"]
    prices = {}
    for mat in info["amount"]:
        if mat == "골드":
            continue
        if mat == frag:
            price = _fragment_unit_price(client)
        else:
            price = _unit_price(client, mat)
        if price is not None:
            prices[mat] = price
    for name in (info["breath"]["name"], info["book"]):
        price = _unit_price(client, name)
        if price is not None:
            prices[name] = price
    return prices
