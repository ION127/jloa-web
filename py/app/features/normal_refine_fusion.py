# -*- coding: utf-8 -*-
"""메인 창 캐시 + 확인 창 판독의 융합 (순수 로직 — Qt·OpenCV 없음).

두 창은 서로 다른 것을 가장 잘 안다:
- 메인 창: 부위·등급  (등급은 21–25단계에서 여기서만 알 수 있다)
- 확인 창: 성공률·장인·숨결  (장인의 기운·실패 스택을 여기서만 알 수 있다)

겹치는 (부위, target) 으로 캐시가 낡았는지 검사한다. 장인의 기운은 메인 창에서 안 읽으므로
검사에 쓰지 않는다.
"""
from dataclasses import dataclass

from app.features import normal_refine as nr


class NeedMainWindow(Exception):
    """등급을 확정할 수 없다 — 메인 재련 창을 한 번 열어야 한다."""


@dataclass(frozen=True)
class MainCache:
    kind: str
    grade: str
    target: int


def cache_from_main(reading):
    if reading is None or not reading.complete():
        return None
    return MainCache(kind=reading.kind, grade=reading.grade, target=reading.target)


def cache_matches(cache, confirm) -> bool:
    """장비를 갈아탔는지 검사. (부위, target) 이 같아야 유효하다.

    장인의 기운은 메인 창에서 안 읽으므로 검사에서 뺀다. 그만큼 검사가 약하지만,
    같은 부위·같은 목표 단계의 다른 등급 장비로 폴링 주기 안에 갈아타야만 오탐이라 실사용에선 드물다.
    """
    if cache is None or confirm is None:
        return False
    return cache.kind == confirm.kind and cache.target == confirm.target


def grade_from_confirm(confirm):
    """숨결 (최대 개수, 1개당 상승률) 만으로 등급이 유일하게 정해질 때만 돌려준다.

    base 는 화면에서 못 읽고(최대는 파생값), '책 존재 여부' 탐지기는 t4_1730 확인 창 캡쳐가 없어
    검증할 수 없으므로 쓰지 않는다. 그래서 성공하는 단계는 12·14·17·18·19 뿐이다.
    """
    if confirm is None or confirm.kind is None or confirm.target is None:
        return None
    if confirm.breath_max is None or confirm.breath_per_unit is None:
        return None
    matches = []
    for grade in nr.grades(confirm.kind):
        row = nr.stage_row(confirm.kind, grade, confirm.target)
        breath = {n: v for n, v in row["breath"].items() if n in nr.BREATH_NAMES}
        if not breath:
            continue
        (max_count, per_unit), = breath.values()
        if (max_count == confirm.breath_max
                and abs(per_unit * 100 - confirm.breath_per_unit) < 0.001):
            matches.append(grade)
    return matches[0] if len(matches) == 1 else None


def _base_percent(kind, grade, target):
    return nr.stage_row(kind, grade, target)["baseProb"] * 100


def advise(confirm, cache, price_map, default_grade=None) -> dict:
    """이번 클릭 조언. recommend_fill() 결과에 grade_source·grade 를 덧붙인다.

    목표는 fill_gold (장인 100% 충전 최저비용). 보유 재료는 화면에서 안 읽으므로 binded 는 넘기지 않는다.
    등급 우선순위: 캐시 → 확인창 숨결 판별 → default_grade(설정값, 2026-07-13 사용자 요청 —
    메인 창 인식 없이 즉시 조언하기 위한 폴백. 한 사람은 보통 한 등급대만 재련한다).
    """
    if confirm is None or not confirm.complete():
        raise NeedMainWindow("확인 창을 읽지 못했습니다")

    if cache_matches(cache, confirm):
        grade, source = cache.grade, "cache"
    else:
        grade, source = grade_from_confirm(confirm), "confirm"
    if grade is None and getattr(confirm, "gear_grade", None):
        # 장비 목록 선택 행의 아이콘 판별 (전율=1730 파란 장식) — 숨결 충돌 단계 커버
        grade, source = confirm.gear_grade, "gear"
    if grade is None and default_grade is not None:
        grade, source = default_grade, "default"
    if grade is None:
        raise NeedMainWindow("메인 재련 창을 한 번 열어주세요")

    base = _base_percent(confirm.kind, grade, confirm.target)
    stack = confirm.fail_stack(base)
    if not (-0.02 <= stack <= base + 0.02):
        raise NeedMainWindow("성공률 판독이 이상합니다 — 다시 시도해 주세요")

    result = nr.recommend_fill(confirm.kind, grade, confirm.target,
                               confirm.jangin, confirm.pure_prob(base), price_map)
    result["grade_source"] = source
    result["grade"] = grade
    result["kind"] = confirm.kind   # 오버레이가 숨결 아이콘(빙하/용암)을 고르는 데 쓴다
    return result
