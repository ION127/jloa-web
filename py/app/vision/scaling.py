"""UI 배율 후보 생성 — 젬·재련 판독기 공통 (ARCHITECTURE §4-1).

로아 UI는 **렌더 세로 해상도**에 비례해 커진다. 템플릿·지오메트리는 1080p 기준이므로
판독기는 앵커를 여러 배율로 매칭해 실제 배율을 **탐색으로 확정**한다(가정하지 않는다).

배율 후보 = (프레임 세로 / 1080) × STEP.

STEP에 1.0 근방(UI 스케일 설정 편차)뿐 아니라 **레터박스 배수**를 넣어야 한다:
16:9 모니터에 로아의 강제 21:9를 걸면 콘텐츠가 위아래로 레터박스되어 UI가 작아진다
(1920 기준 콘텐츠 세로 ≈ 823px → 약 0.76 × base). 로아의 강제 화면비는 21:9까지이므로
그보다 깊은 레터박스는 후보에 두지 않는다.
네이티브 울트라와이드(2560×1080, 3840×1080, 5120×1440)는 세로가 배율을 정하므로
STEP 1.0으로 잡힌다 — 가로가 넓어져도 앵커 상대 좌표라 영향이 없다.

**후보는 대략만 맞으면 된다.** 진짜 배율은 fine_scale_candidates로 정련하므로(±6%),
격자가 실제 배율(강제 21:9 실측 0.746)을 정확히 담을 필요는 없다.
"""

BASE_HEIGHT = 1080        # 템플릿·지오메트리 기준 세로 해상도

# 강제 21:9 레터박스(≈0.76) + UI 스케일 편차(0.80~1.30)
_LETTERBOX_STEPS = (0.7620,)
_NATIVE_STEPS = (0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.20, 1.30)
SCALE_STEPS = tuple(sorted(_LETTERBOX_STEPS + _NATIVE_STEPS))

# 정련(±6%)까지 감안해도 0.70 아래로 내려갈 일이 없다 — 오탐 여지를 줄인다.
_MIN_SCALE, _MAX_SCALE = 0.65, 3.0


FINE_SPAN = 0.06     # 정련 범위 (±6%)
FINE_STEP = 0.004    # 정련 간격


def fine_scale_candidates(scale0: float, span: float = FINE_SPAN,
                          step: float = FINE_STEP):
    """coarse로 고른 배율 주변을 촘촘히 훑을 후보 (배율 정련용).

    후보 격자는 `base = 프레임높이/1080`에 묶여 있지만, **진짜 UI 배율은 프레임 높이와
    무관하다** — 로아 강제 화면비에선 게임 내부 레터박스 콘텐츠 높이가 배율을 정한다.
    그래서 창 높이가 1080/1075/1088 처럼 몇 px만 달라져도 격자가 진짜 배율을 빗나가
    앵커 점수가 뚝 떨어진다 (실측 2026-07-10: 진짜 0.746인데 격자는 0.750/0.7613/0.7676,
    점수 0.93 → 0.86/0.76/0.65 → 1088 높이에서 기각 = '창을 움직이면 인식되는' 증상).
    → 최고 후보 주변을 연속으로 정련해 실제 배율을 찾는다.
    """
    count = int(round(span / step))
    out = []
    for i in range(-count, count + 1):
        scale = round(scale0 + i * step, 4)
        if _MIN_SCALE <= scale <= _MAX_SCALE:
            out.append(scale)
    return out


def snap(scale: float, grid: float = 0.05) -> float:
    return round(round(scale / grid) * grid, 4)


def pick_refined(results, eps: float = 1e-6):
    """정련 결과 [(점수, 배율, 위치)] → 채택할 하나.

    템플릿을 리샘플하면 이웃 배율이 같은 픽셀 크기로 반올림돼 **점수 평탄구간**이
    생긴다 (실측: 1080p 재련 앵커에서 0.996/1.000/1.004가 모두 conf 1.00000).
    첫 최고점을 그냥 집으면 0.996 같은 값이 잡혀, 먼 영역 좌표가 1px씩 밀려
    좁은 밴드 판독이 깨진다. → 평탄구간에선 0.05 격자에 가장 가까운 배율을 고른다.
    """
    best_conf = max(conf for conf, _, _ in results)
    plateau = [r for r in results if r[0] >= best_conf - eps]
    return min(plateau, key=lambda r: (abs(r[1] - snap(r[1])), r[1]))


def anchor_threshold(base: float, scale: float) -> float:
    """이 배율에서 요구할 앵커 정밀 매칭 임계.

    1080p 템플릿을 1.0 미만으로 리샘플하면 글자 획이 뭉개져 상관계수가 구조적으로
    낮아진다 — 임계를 그대로 두면 **정답 배율을 찾아 놓고도 기각**한다.
    배율 정련 후 강제 21:9 실측 점수는 0.92~0.93이라 여유가 크지만, 리샘플 손실을
    감안해 축소 구간은 0.70으로 둔다. 후보 중 최고 점수만 채택하므로 오탐 여지는 작다.
    """
    if scale >= 0.98:
        return base
    return min(base, 0.70)


def scale_candidates(frame_height: int):
    """이 프레임에서 시도할 UI 배율 후보 (base에 가까운 순).

    base(=세로/1080)에 가까운 것부터 반환해, 흔한 네이티브 해상도를 먼저 맞춘다.
    정확한 격자(0.05)에 근접하면 스냅한다 — 앵커를 리샘플해 찾으면 배율이 0.999처럼
    미세하게 어긋나고, 좌표를 반올림하며 좁은 밴드가 1px씩 밀려 판독이 실패한다(실측).
    """
    base = frame_height / BASE_HEIGHT
    candidates = []
    for step in SCALE_STEPS:
        scale = base * step
        snapped = round(scale * 20) / 20
        if abs(scale - snapped) < 0.01:
            scale = snapped
        candidates.append(round(scale, 4))
    ordered = sorted(set(candidates), key=lambda s: abs(s - base))
    return [s for s in ordered if _MIN_SCALE <= s <= _MAX_SCALE]
