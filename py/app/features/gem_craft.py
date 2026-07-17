"""아크그리드 젬 가공 기대값 엔진 (Phase 6.1, 순수 로직 — Qt 무관).

게임 규칙 (공식 확률 공시, app/data/gem_craft.json):
- 매 가공 차수에 4개의 가능성이 등장(중복 없음)하고, "가공하기"를 누르면
  4개 중 하나가 25% 균등 추첨으로 적용된다 — 플레이어가 효과를 고를 수 없다.
- 플레이어의 유일한 의사결정: 이 4개로 가공할지 / "다른 항목 보기"(리롤)할지.
  보기는 가공을 1회 이상 진행해야 사용 가능하며 가공 횟수를 소모하지 않는다.
- 각 가능성의 등장 확률 = 표기확률 / (100% - 미등장 가능성의 표기확률 합).
- 스탯 4종(의지력 효율/포인트/효과1/효과2)은 Lv1~5. "20젬" = 4종 합 20.

가치 모델 (2026-07-08 사용자 확인):
- 젬의 사용 가치는 **질서/혼돈 포인트 5 + 의지력 효율 4 이상**이 필수 조건.
  이게 안 되면 효과 레벨이 높아도 쓸모없다. 효과 2종은 그 위의 부가 가치.
- 목표는 GemTarget(포인트 최소/효율 최소/효과 합 최소/총합 최소)의 AND 조건.

계산 방식:
- 효과1/효과2는 확률 분포가 동일하고 목표도 합으로만 보므로 둘만 정렬해
  정규형으로 캐시한다 (효율·포인트는 목표가 구분하므로 정렬 불가).
- V(상태) = E[제시 4개 조합][ max(가공 기대값, 리롤 기대값) ].
  조합 분포는 가중 비복원 추출이라 전수 열거가 비싸서, 상태별 시드 고정
  샘플링(기본 120회)으로 근사한다 — 같은 입력이면 항상 같은 결과.
- 비용(±100%) 상태는 v1에서 무시한다: 등장 확률 재정규화에 주는 영향이
  최대 3.5%p 수준이고 성공 확률에는 직접 영향이 없다.
- 게임의 "초기화(1/1)" 기능은 아직 미모델링 (규칙 확인 후 v2).
"""

import array
import json
import os
import random
from dataclasses import dataclass, replace

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "gem_craft.json")

with open(_DATA_PATH, encoding="utf-8") as _f:
    DATA = json.load(_f)

GRADES = DATA["grades"]
STAT_MAX = DATA["stat_max"]
STATS = tuple(DATA["stats"])  # (willpower, points, effect1, effect2)
POOL = {entry["id"]: entry for entry in DATA["pool"]}


@dataclass(frozen=True)
class GemState:
    willpower: int
    points: int
    effect1: int
    effect2: int
    attempts: int   # 남은 가공 횟수
    rerolls: int    # 남은 "다른 항목 보기" 횟수

    def levels(self) -> tuple:
        return (self.willpower, self.points, self.effect1, self.effect2)

    def total(self) -> int:
        return sum(self.levels())


@dataclass(frozen=True)
class GemTarget:
    """모든 조건의 AND. 기본값 = 발사대 최소 조건 (포인트 5 + 효율 4).

    2026-07-09 사용자 기준:
    - 질서/혼돈 포인트는 **무조건 5**여야 쓸 수 있다. 효율은 4 이상.
      → 발사대는 (효율4·포인트5)와 (효율5·포인트5) 두 가지뿐이다.
    - 유효유물/유효고대는 포인트 5 + 효율 5를 깔고 **총합 16+ / 19+**.
    """
    points_min: int = 5
    willpower_min: int = 4
    effects_sum_min: int = 0   # 효과1+효과2 최소 합 (0 = 조건 없음)
    total_min: int = 0         # 4스탯 총합 최소 (0 = 조건 없음)

    def satisfied(self, state: GemState) -> bool:
        return (state.points >= self.points_min
                and state.willpower >= self.willpower_min
                and state.effect1 + state.effect2 >= self.effects_sum_min
                and state.total() >= self.total_min)

    def label(self) -> str:
        parts = []
        if self.points_min > 0:
            parts.append(f"포인트 {self.points_min}+")
        if self.willpower_min > 0:
            parts.append(f"효율 {self.willpower_min}+")
        if self.effects_sum_min > 0:
            parts.append(f"효과 합 {self.effects_sum_min}+")
        if self.total_min > 0:
            parts.append(f"총합 {self.total_min}+")
        return " · ".join(parts) if parts else "조건 없음"


# 표준 목표 (2026-07-09 사용자 정정). 포인트 5는 모든 목표의 전제 조건이다.
# 발사대는 효율 4 또는 5, 유효유물/유효고대는 효율 5 위에 총합 16+ / 19+.
STANDARD_TARGETS = [
    ("발사대 효율4·포인트5", GemTarget(points_min=5, willpower_min=4)),
    ("발사대 효율5·포인트5", GemTarget(points_min=5, willpower_min=5)),
    ("유효유물 (55 + 총합16+)", GemTarget(points_min=5, willpower_min=5, total_min=16)),
    ("유효고대 (55 + 총합19+)", GemTarget(points_min=5, willpower_min=5, total_min=19)),
    ("완젬 (총합20)", GemTarget(points_min=5, willpower_min=5, effects_sum_min=10)),
]

# 가공/보기/초기화 추천을 판단할 기본 기준 목표 (발사대 효율5·포인트5).
# STANDARD_TARGETS 순서가 바뀌어도 UI가 따라오도록 한 곳에서만 정한다.
DEFAULT_TARGET_INDEX = 1


def initial_state(grade: str) -> GemState:
    info = GRADES[grade]
    lv = DATA["initial_level"]
    return GemState(lv, lv, lv, lv, info["attempts"], info["rerolls"])


class GemDpTable:
    """오프라인 전수 계산 DP 테이블 (tools/build_gem_dp.py 산출물) 조회.

    gempago(loatto.kr)와 같은 '사전 계산 테이블 조회' 구조 — 차이는 우리 테이블이
    제시 4개 조합 분포까지 정확 열거한 전수 계산이라 샘플링 오차가 없다는 것.
    런타임 의존성 없음 (표준 라이브러리 array로 float32 바이너리 로드)."""

    def __init__(self, bin_path: str, meta: dict):
        self._data = array.array("f")
        with open(bin_path, "rb") as f:
            self._data.frombytes(f.read())
        self.a_max = meta["a_max"]
        self.r_max = meta["r_max"]
        self.reset_max = meta["reset_max"]
        self.layers = meta["layers"]
        self._grade_index = {grade: i for i, grade in enumerate(meta["grades"])}
        self._pair_index = {tuple(pair): i for i, pair in enumerate(meta["pairs"])}
        self._targets = {
            (t["points_min"], t["willpower_min"], t["effects_sum_min"], t["total_min"]): i
            for i, t in enumerate(meta["targets"])
        }
        expected = (len(self._targets) * self.layers * 5 * 5 * 15
                    * (self.a_max + 1) * (self.r_max + 1))
        if len(self._data) != expected:
            raise ValueError(f"gem_dp.bin 크기 불일치: {len(self._data)} != {expected}")

    def target_index(self, target: GemTarget):
        return self._targets.get((target.points_min, target.willpower_min,
                                  target.effects_sum_min, target.total_min))

    def _layer(self, resets_left: int, grade) -> int:
        """초기화 0회는 등급 무관 공용 층. 1회 이상은 등급별 층
        (초기화는 그 등급의 초기 상태로 되돌아가므로 하한값이 다르다)."""
        if resets_left <= 0:
            return 0
        grade_index = self._grade_index.get(grade)
        if grade_index is None:
            return 0  # 등급을 모르면 초기화를 세지 않는다 (보수적)
        resets = min(resets_left, self.reset_max)
        return 1 + grade_index * self.reset_max + (resets - 1)

    def lookup(self, target_idx: int, state: GemState,
               resets_left: int = 0, grade=None) -> float:
        e1, e2 = state.effect1, state.effect2
        if e1 > e2:
            e1, e2 = e2, e1
        pair = self._pair_index[(e1, e2)]
        a = min(max(state.attempts, 0), self.a_max)
        r = min(max(state.rerolls, 0), self.r_max)
        idx = (((((target_idx * self.layers + self._layer(resets_left, grade)) * 5
                  + state.willpower - 1) * 5 + state.points - 1)
                * 15 + pair) * (self.a_max + 1) + a) * (self.r_max + 1) + r
        return self._data[idx]


_DP_TABLE = None
_DP_TABLE_LOADED = False


def load_dp_table():
    """테이블 lazy 로드 (파일 없으면 None — 엔진이 샘플링으로 폴백)."""
    global _DP_TABLE, _DP_TABLE_LOADED
    if not _DP_TABLE_LOADED:
        _DP_TABLE_LOADED = True
        bin_path = os.path.join(_DATA_DIR := os.path.dirname(_DATA_PATH), "gem_dp.bin")
        meta_path = os.path.join(_DATA_DIR, "gem_dp.json")
        if os.path.exists(bin_path) and os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                _DP_TABLE = GemDpTable(bin_path, json.load(f))
    return _DP_TABLE


def entry_label(entry_id: str) -> str:
    """UI 표시용 라벨 (게임 창의 문구와 대응)."""
    entry = POOL[entry_id]
    kind = entry["kind"]
    if kind == "stat":
        name = DATA["stat_names"][entry["stat"]]
        delta = entry["delta"]
        return f"{name} {'+' if delta > 0 else ''}{delta}"
    if kind == "change":
        return f"{DATA['stat_names'][entry['stat']]} 변경"
    if kind == "cost":
        return f"가공 비용 {'+' if entry['delta'] > 0 else ''}{entry['delta']}%"
    if kind == "keep":
        return "가공 상태 유지"
    return f"다른 항목 보기 +{entry['delta']}회"


def available_entries(state: GemState) -> list:
    """현재 상태에서 등장 가능한 (entry, 표기확률) — 공시의 미등장 조건 적용.

    비용 ±는 비용 상태 극값 조건을 v1에서 무시하고 '마지막 1회' 조건만 반영.
    """
    result = []
    levels = {stat: getattr(state, stat) for stat in STATS}
    for entry in POOL.values():
        kind = entry["kind"]
        if kind == "stat":
            level = levels[entry["stat"]]
            delta = entry["delta"]
            if delta > 0 and level + delta > STAT_MAX:
                continue
            if delta < 0 and level <= DATA["stat_min"]:
                continue
        elif kind in ("cost", "reroll") and state.attempts <= 1:
            continue
        result.append((entry, entry["prob"]))
    return result


def apply_entry(state: GemState, entry_id: str) -> GemState:
    """가공 결과 적용 — 어떤 효과든 가공 횟수 1회를 소모한다."""
    entry = POOL[entry_id]
    state = replace(state, attempts=state.attempts - 1)
    if entry["kind"] == "stat":
        value = getattr(state, entry["stat"]) + entry["delta"]
        value = max(DATA["stat_min"], min(STAT_MAX, value))
        return replace(state, **{entry["stat"]: value})
    if entry["kind"] == "reroll":
        return replace(state, rerolls=state.rerolls + entry["delta"])
    return state  # change/cost/keep — 합 목표 기준으로는 변화 없음


def draw_four(rng: random.Random, entries: list) -> list:
    """가중 비복원 추출로 4개 가능성 결정 (게임의 '자동 결정' 재현)."""
    pool = list(entries)
    picked = []
    for _ in range(min(4, len(pool))):
        weights = [w for _, w in pool]
        chosen = rng.choices(range(len(pool)), weights=weights)[0]
        picked.append(pool.pop(chosen)[0])
    return picked


# 내부 고속 경로용 사전 컴파일: id → (0, 스탯 인덱스, 델타) | (1, 보기 증가) | (2,)
_OP_STAT, _OP_REROLL, _OP_NOOP = 0, 1, 2
_OPS = {}
_POOL_COMPILED = []  # (op, 표기확률, 마지막 1회 미등장 여부) — 순서는 공시 풀 순서
for _entry in DATA["pool"]:
    if _entry["kind"] == "stat":
        _op = (_OP_STAT, STATS.index(_entry["stat"]), _entry["delta"])
    elif _entry["kind"] == "reroll":
        _op = (_OP_REROLL, _entry["delta"])
    else:
        _op = (_OP_NOOP,)
    _OPS[_entry["id"]] = _op
    # 마지막 1회 미등장은 비용·보기만 — 효과 변경/유지는 끝까지 등장 (공시 미등장 조건)
    _POOL_COMPILED.append((_op, _entry["prob"], _entry["kind"] in ("cost", "reroll")))


class GemCraftEngine:
    """목표 달성 확률·가공/리롤 추천. 같은 입력 → 같은 출력 (상태별 시드 고정).

    내부 재귀는 int 튜플로만 동작한다 (dataclass 생성/해시가 병목이었음 —
    2026-07-08 최적화로 초기 상태 7목표 계산 180s → 수 초 수준)."""

    def __init__(self, samples: int = 120, seed: int = 20260708):
        self.samples = samples
        self.seed = seed
        self._cache = {}
        self._shown_cache = {}  # 풀 시그니처 → 샘플된 제시 4개 세트들 (목표·차수 무관 재사용)
        self._marginal_cache = {}  # 풀 시그니처 → 효과별 제시 포함 확률 (정확)

    def _shown_sets(self, levels: tuple, last_attempt: bool) -> list:
        """이 레벨 조합에서 등장 가능한 '제시 4개' 샘플 목록 (op 튜플로 컴파일).

        등장 풀은 (스탯 레벨, 마지막 1회 여부)로만 정해지므로 그 단위로 1회만
        추첨해 캐시한다 — 목표 7종·남은 횟수 전체에서 재사용."""
        key = (levels, last_attempt)
        sets = self._shown_cache.get(key)
        if sets is None:
            pool = []
            for op, prob, last_excluded in _POOL_COMPILED:
                if op[0] == _OP_STAT:
                    level = levels[op[1]]
                    if op[2] > 0 and level + op[2] > STAT_MAX:
                        continue
                    if op[2] < 0 and level <= 1:
                        continue
                elif last_attempt and last_excluded:  # 비용·보기 증가만 마지막 1회 미등장
                    continue
                pool.append((op, prob))
            rng = random.Random(f"{self.seed}:{key}")
            sets = []
            for _ in range(self.samples):
                remaining = list(pool)
                shown = []
                for _pick in range(4):
                    weights = [w for _, w in remaining]
                    chosen = rng.choices(range(len(remaining)), weights=weights)[0]
                    shown.append(remaining.pop(chosen)[0])
                sets.append(tuple(shown))
            self._shown_cache[key] = sets
        return sets

    def _value(self, w, p, e1, e2, attempts, rerolls, can_reroll, tp) -> float:
        """tp = (points_min, willpower_min, effects_sum_min, total_min)."""
        pm, wm, em, tm = tp
        total = w + p + e1 + e2
        if p >= pm and w >= wm and e1 + e2 >= em and total >= tm:
            return 1.0
        if attempts <= 0:
            return 0.0
        # 불가능 가지치기 (한 차수는 스탯 하나만 최대 +4, 서로 다른 스탯은 별도 차수)
        pd = pm - p if pm > p else 0
        wd = wm - w if wm > w else 0
        ed = em - e1 - e2 if em > e1 + e2 else 0
        td = tm - total if tm > total else 0
        if ed > 2 * STAT_MAX - e1 - e2:
            return 0.0
        if (pd + 3) // 4 + (wd + 3) // 4 + (ed + 3) // 4 > attempts:
            return 0.0
        needed = pd + wd + ed
        if td > needed:
            needed = td
        cap = 4 * attempts
        headroom = 4 * STAT_MAX - total
        if headroom < cap:
            cap = headroom
        if needed > cap:
            return 0.0

        if e1 > e2:
            e1, e2 = e2, e1  # 효과1/효과2 대칭 정규화 (분포·목표 모두 대칭)
        can_reroll = can_reroll and rerolls > 0
        key = (w, p, e1, e2, attempts, rerolls, can_reroll, tp)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        reroll_value = 0.0
        if can_reroll:
            reroll_value = self._value(w, p, e1, e2, attempts, rerolls - 1, True, tp)

        value_fn = self._value
        next_attempts = attempts - 1
        acc = 0.0
        for shown in self._shown_sets((w, p, e1, e2), attempts <= 1):
            branch = 0.0
            for op in shown:
                kind = op[0]
                if kind == _OP_STAT:
                    idx, delta = op[1], op[2]
                    nw, np_, ne1, ne2 = w, p, e1, e2
                    if idx == 0:
                        nw += delta
                    elif idx == 1:
                        np_ += delta
                    elif idx == 2:
                        ne1 += delta
                    else:
                        ne2 += delta
                    branch += value_fn(nw, np_, ne1, ne2, next_attempts, rerolls, True, tp)
                elif kind == _OP_REROLL:
                    branch += value_fn(w, p, e1, e2, next_attempts, rerolls + op[1], True, tp)
                else:
                    branch += value_fn(w, p, e1, e2, next_attempts, rerolls, True, tp)
            branch *= 0.25
            acc += branch if branch > reroll_value else reroll_value
        value = acc / self.samples
        self._cache[key] = value
        return value

    def _marginals(self, levels: tuple, last_attempt: bool) -> list:
        """각 효과가 '제시 4개'에 포함될 확률 m_e (정확 전수) — [(op, m_e)].

        가공(무보기) 기대값 = ¼ Σ_e m_e · V(자식) (25% 균등 추첨의 선형성)."""
        key = (levels, last_attempt)
        cached = self._marginal_cache.get(key)
        if cached is not None:
            return cached
        pool = []
        for op, prob, last_excluded in _POOL_COMPILED:
            if op[0] == _OP_STAT:
                level = levels[op[1]]
                if op[2] > 0 and level + op[2] > STAT_MAX:
                    continue
                if op[2] < 0 and level <= 1:
                    continue
            elif last_attempt and last_excluded:
                continue
            pool.append((op, prob))
        total = sum(prob for _, prob in pool)
        # f(T) 크기별 전개 (tools/build_gem_dp.py와 동일 점화식)
        level_prev = {0: (1.0, 0.0)}
        for _size in range(4):
            level_now = {}
            for mask, (f_prev, s_prev) in level_prev.items():
                for x, (_op, weight) in enumerate(pool):
                    bit = 1 << x
                    if mask & bit:
                        continue
                    new_mask = mask | bit
                    add = f_prev * weight / (total - s_prev)
                    entry = level_now.get(new_mask)
                    if entry is None:
                        level_now[new_mask] = (add, s_prev + weight)
                    else:
                        level_now[new_mask] = (entry[0] + add, entry[1])
            level_prev = level_now
        marginals = [0.0] * len(pool)
        for mask, (f_value, _s) in level_prev.items():
            for x in range(len(pool)):
                if mask & (1 << x):
                    marginals[x] += f_value
        result = [(op, m) for (op, _w), m in zip(pool, marginals)]
        self._marginal_cache[key] = result
        return result

    def _child_states(self, state: GemState):
        """(자식 상태, 제시 4개에 포함될 확률) — 가공 기대값 계산용 (정확)."""
        levels = state.levels()
        for op, marginal in self._marginals(levels, state.attempts <= 1):
            if op[0] == _OP_STAT:
                w, p, e1, e2 = levels
                idx, delta = op[1], op[2]
                if idx == 0:
                    w += delta
                elif idx == 1:
                    p += delta
                elif idx == 2:
                    e1 += delta
                else:
                    e2 += delta
                child = GemState(w, p, e1, e2, state.attempts - 1, state.rerolls)
            elif op[0] == _OP_REROLL:
                child = replace(state, attempts=state.attempts - 1,
                                rerolls=state.rerolls + op[1])
            else:
                child = replace(state, attempts=state.attempts - 1)
            yield child, marginal

    def _table_no_reroll(self, table, target_idx, state, resets_left, grade) -> float:
        """보기가 아직 잠긴 상태(가공 0회)의 정확값 — 이번 차수는 무조건 가공."""
        acc = sum(marginal * table.lookup(target_idx, child, resets_left, grade)
                  for child, marginal in self._child_states(state))
        return acc / 4.0

    def success_probability(self, state: GemState, target: GemTarget,
                            can_reroll: bool = True, resets_left: int = 0,
                            grade: str = None) -> float:
        """지금부터 최적 플레이(가공/보기/초기화 중 최선) 시 목표 달성 확률.

        초기화를 세려면 grade가 필요하다 — 초기화는 그 등급의 초기 상태로 돌아가므로
        (희귀는 7회/보기1, 영웅은 9회/보기2). grade를 주지 않으면 초기화 0회로 본다.
        표준 목표 7종은 전수 DP 테이블 조회(정확·즉시), 그 외 목표는 샘플링 폴백
        (샘플링 폴백은 초기화를 모델링하지 않는다)."""
        tp = (target.points_min, target.willpower_min,
              target.effects_sum_min, target.total_min)
        if target.satisfied(state):
            return 1.0
        table = load_dp_table()
        if table is not None:
            target_idx = table.target_index(target)
            if target_idx is not None:
                if state.attempts <= 0:
                    # 가공 횟수 소진 — 초기화가 남아 있으면 처음부터 다시 가능
                    if resets_left <= 0 or grade not in GRADES:
                        return 0.0
                    return table.lookup(target_idx, state, resets_left, grade)
                if can_reroll or state.rerolls == 0:
                    return table.lookup(target_idx, state, resets_left, grade)
                return self._table_no_reroll(table, target_idx, state, resets_left, grade)
        if state.attempts <= 0:
            return 0.0
        return self._value(state.willpower, state.points, state.effect1, state.effect2,
                           state.attempts, state.rerolls, can_reroll, tp)

    def recommend_action(self, state: GemState, target: GemTarget,
                         shown_ids: list = None, grade: str = None,
                         resets_left: int = 0, can_reroll: bool = True) -> dict:
        """게임 화면 4버튼(가공 하기/다른 항목 보기/초기화/가공 완료) 중 추천 행동.

        **값을 두 갈래로 계산한다** (2026-07-10):

        1) 결정용 `process`/`reroll`/`reset` — 초기화를 '이후에도 쓸 수 있는 옵션'으로
           포함한다(resets_left 그대로). 초기화는 가공 횟수를 소모하지 않으므로 남은
           초기화 r회는 이후 가치의 **하한**이 된다. 이 하한이 있어야 "갓 시작한 젬에서
           초기화를 권하지 않는다" 같은 판단이 맞는다 (하한을 빼면 '초기화하면 가공
           횟수가 복구되니 지금 당장 초기화'라는 엉뚱한 추천이 나온다).

        2) 표시용 `per_choice`/`this_gem` — 초기화를 **배제**하고(resets_left=0) 계산한다.
           하한을 포함하면 새 젬 가치가 모든 상태의 바닥으로 깔려, 스탯·남은 횟수·제시된
           4개가 무엇이든 전부 같은 수치로 뭉개진다 (실측: 서로 다른 젬 3개의 목표 5종과
           보기 4개가 전부 10.669% = 초기화 값). 사용자가 보려는 "지금 이 젬으로, 이 4개
           앞에서 목표에 닿을 확률"은 이쪽이다.

        - 가공 완료: 목표를 이미 달성했거나, 어떤 행동으로도 확률이 0일 때
        - shown_ids가 없으면(선택지 미인식) 가공 기대값은 제시 분포의 기대값(정확)

        반환: {"action", "process", "reroll", "reset", "per_choice", "this_gem"}
          process/reroll/reset : 결정 기준 (초기화 하한 포함)
          per_choice           : 제시 4개 각각을 적용했을 때 **이 젬으로**의 달성 확률
          this_gem             : 초기화 없이 이 젬으로 최적 플레이 시 달성 확률
        """
        if target.satisfied(state):
            return {"action": "가공 완료", "process": 1.0, "reroll": None,
                    "reset": None, "per_choice": [], "this_gem": 1.0}

        def value(st, *, floor, can_rr=True):
            """floor=True면 초기화를 이후 옵션으로 포함(하한), False면 이 젬만."""
            return self.success_probability(
                st, target, can_reroll=can_rr,
                resets_left=resets_left if floor else 0,
                grade=grade if floor else None)

        # --- 표시용: 이 젬으로 (초기화 배제) ---
        per_choice = []
        if state.attempts <= 0:
            this_process = 0.0
        elif shown_ids:
            per_choice = [{
                "id": entry_id, "label": entry_label(entry_id),
                "probability": value(apply_entry(state, entry_id), floor=False),
            } for entry_id in shown_ids]
            this_process = sum(c["probability"] for c in per_choice) / len(per_choice)
        else:
            this_process = value(state, floor=False, can_rr=False)
        this_reroll = None
        if can_reroll and state.rerolls > 0 and state.attempts > 0:
            this_reroll = value(replace(state, rerolls=state.rerolls - 1), floor=False)
        this_gem = max(this_process, this_reroll or 0.0)

        # reset 비교용 '이 젬의 정직한 값' — 이번 턴 새로고침까지 허용해 계산한다.
        # this_process 는 can_rr=False(이번 턴 강제 가공)라 새 젬을 0.0001 수준으로 과소평가해,
        # 갓 시작한 젬조차 reset 보다 낮게 나온다. can_rr=True 로 재면 새 젬은 reset 과 정확히
        # 같아져(같은 계산) 새 젬에서 초기화를 권하지 않는다.
        this_gem_full = (value(state, floor=False, can_rr=True)
                         if state.attempts > 0 else 0.0)

        # --- 결정용: 초기화 하한 포함 ---
        if state.attempts <= 0:
            process = 0.0
        elif shown_ids:
            process = sum(value(apply_entry(state, e), floor=True)
                          for e in shown_ids) / len(shown_ids)
        else:
            process = value(state, floor=True, can_rr=False)

        reroll = None
        if can_reroll and state.rerolls > 0 and state.attempts > 0:
            reroll = value(replace(state, rerolls=state.rerolls - 1), floor=True)
        reset = None
        if resets_left > 0 and grade in GRADES:
            # 초기화하면 그 등급의 처음 상태로 돌아가고 초기화 보유가 1 줄어든다
            reset = self.success_probability(
                initial_state(grade), target, resets_left=resets_left - 1, grade=grade)

        # 결정은 '이 젬의 정직한 값'(초기화 하한 없이)으로 한다. process/reroll(하한 포함)로
        # 비교하면 하한이 늘 reset 을 이겨서, 사실상 절망적인 젬에도 900골드 가공을 권한다
        # (2026-07-12 실측·사용자 지적). this_gem = max(가공, 새로고침)을 reset 과 비교한다.
        action = "가공"
        if this_reroll is not None and this_reroll > this_process:
            action = "다른 항목 보기"   # 동점이면 가공 (보기 횟수를 아낀다)

        if reset is not None and reset > this_gem_full + 1e-9:
            # 이 젬은 그 등급 새 젬을 처음부터 깎는 것보다 못하다. 새로고침이 남았으면
            # (공짜에 가까움) 그걸 먼저 — 종자적으로 더 나은 판을 노린다. 없으면 초기화
            # (확률이 낮은 가공에 900골드를 붓지 않는다).
            action = "다른 항목 보기" if (state.rerolls > 0 and state.attempts > 0) else "초기화"
        elif this_gem <= 0.0:
            action = "가공 완료"  # 달성 불가(초기화로도 안 됨) — 남은 가공 골드 절약
        return {"action": action, "process": process, "reroll": reroll,
                "reset": reset, "per_choice": per_choice, "this_gem": this_gem}

    def recommend_all(self, state: GemState, shown_ids: list = None,
                      grade: str = None, resets_left: int = 0,
                      can_reroll: bool = True) -> list:
        """표준 목표 7종 각각의 (라벨, 달성 확률, 추천 행동) — 오버레이 표시용.

        발사대는 그냥 가공해도 되지만 유효유물/유효고대를 노리면 새로고침·초기화가
        나은 경우가 갈린다 → 목표마다 따로 판단해 함께 보여준다.

        '달성 확률'은 **지금 이 젬으로, 지금 제시된 4개 앞에서** 최적 플레이 시 목표
        달성 확률이다(초기화 제외 — recommend_action 주석 참고). 스탯·남은 가공 횟수·
        제시된 4개가 모두 반영되므로 젬마다·차수마다 값이 달라진다.

        초기화는 detail["reset"]에 '새 젬으로 다시 시작할 때의 확률'로 따로 담긴다.
        (2026-07-09: 제시 무관한 상태 주변화값을 보여 주던 것을 고침.
         2026-07-10: 초기화 값이 하한으로 깔려 모든 값이 같아지던 것을 고침.)"""
        rows = []
        for label, target in STANDARD_TARGETS:
            result = self.recommend_action(
                state, target, shown_ids=shown_ids, grade=grade,
                resets_left=resets_left, can_reroll=can_reroll)
            rows.append({
                "label": label,
                "probability": result["this_gem"],
                "action": result["action"],
                "detail": result,
            })
        return rows

    def evaluate_shown(self, state: GemState, shown_ids: list, target: GemTarget,
                       can_reroll: bool = True) -> dict:
        """실제로 제시된 4개 앞에서의 판단.

        반환: process(가공 시 달성 확률), reroll(리롤 시, 불가하면 None),
        recommend("가공" | "다른 항목 보기"), per_choice(효과별 적용 후 확률).
        """
        per_choice = []
        for entry_id in shown_ids:
            after = apply_entry(state, entry_id)
            per_choice.append({
                "id": entry_id,
                "label": entry_label(entry_id),
                "probability": self.success_probability(after, target),
            })
        process = sum(c["probability"] for c in per_choice) / len(per_choice)

        reroll = None
        if can_reroll and state.rerolls > 0:
            reroll = self.success_probability(
                replace(state, rerolls=state.rerolls - 1), target, can_reroll=True)

        recommend = "가공"
        if reroll is not None and reroll > process:
            recommend = "다른 항목 보기"
        return {"process": process, "reroll": reroll,
                "recommend": recommend, "per_choice": per_choice}
