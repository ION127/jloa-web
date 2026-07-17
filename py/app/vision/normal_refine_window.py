# -*- coding: utf-8 -*-
"""메인 재련 창 판독기. 앵커는 "재련 비용" 라벨 (게임 상태 불변)."""
import os
from dataclasses import dataclass, field

from app.features import normal_refine as nr
from app.vision import glyphs
from app.vision import normal_refine_geometry as g
from app.vision import scaling

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "vision", "normal_refine")

_COARSE_SCALE = 0.25
_COARSE_CANDIDATES_PER_SCALE = 2
_PRECISE_CANDIDATES = 8


def _cv2():
    import cv2
    return cv2


class _AnchorReader:
    """앵커 탐색 공통부. 하위 클래스가 _anchor_file 을 정한다."""

    _anchor_file = None

    def __init__(self, data_dir=_DATA_DIR):
        self._data_dir = data_dir
        self._anchor_gray = None
        self._anchor_cache = {}

    def anchor_gray(self):
        if self._anchor_gray is None:
            import numpy as np
            cv2 = _cv2()
            path = os.path.join(self._data_dir, self._anchor_file)
            # repo 경로에 한글이 섞여 있으면 cv2.imread가 조용히 실패한다(Windows
            # fopen 인코딩 문제) — imdecode + np.fromfile 로 우회한다.
            image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise FileNotFoundError(path)
            self._anchor_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return self._anchor_gray

    def _anchor_at(self, scale):
        """Return cached full-resolution and coarse anchor templates."""
        cached = self._anchor_cache.get(scale)
        if cached is not None:
            return cached

        cv2 = _cv2()
        template = self.anchor_gray()
        if scale == 1.0:
            full = template
        else:
            height = max(1, int(round(template.shape[0] * scale)))
            width = max(1, int(round(template.shape[1] * scale)))
            full = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
        small = cv2.resize(full, None, fx=_COARSE_SCALE, fy=_COARSE_SCALE,
                           interpolation=cv2.INTER_AREA)
        cached = (full, small)
        self._anchor_cache[scale] = cached
        return cached

    def _score_near(self, gray, top_left, margin, scale):
        """Match only a small full-resolution area around a candidate."""
        cv2 = _cv2()
        template, _ = self._anchor_at(scale)
        th, tw = template.shape
        x, y = top_left
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1 = min(gray.shape[1], x + tw + margin)
        y1 = min(gray.shape[0], y + th + margin)
        region = gray[y0:y1, x0:x1]
        if region.shape[0] < th or region.shape[1] < tw:
            return None, -1.0
        result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        return (x0 + location[0], y0 + location[1]), float(score)

    def _match_near(self, gray, top_left, margin, scale):
        location, score = self._score_near(gray, top_left, margin, scale)
        if (location is None
                or score < scaling.anchor_threshold(0.80, scale)):
            return None
        return location

    def _refine_scale(self, gray, location, scale0):
        """Use full-resolution local matching to select the final UI scale."""
        refined = []
        for scale in scaling.fine_scale_candidates(scale0):
            found, score = self._score_near(gray, location, 10, scale)
            if found is not None:
                refined.append((score, scale, found))
        if not refined:
            return location, -1.0, scale0
        score, scale, found = scaling.pick_refined(refined)
        return found, score, scale

    @staticmethod
    def _coarse_peaks(result, template_shape):
        """Return separated local maxima from one downscaled match result."""
        cv2 = _cv2()
        work = result.copy()
        th, tw = template_shape
        peaks = []
        for _ in range(_COARSE_CANDIDATES_PER_SCALE):
            _, score, _, location = cv2.minMaxLoc(work)
            peaks.append((float(score), location))
            x, y = location
            work[max(0, y - th // 2):min(work.shape[0], y + th // 2 + 1),
                 max(0, x - tw // 2):min(work.shape[1], x + tw // 2 + 1)] = -1.0
        return peaks

    def find_anchor(self, frame_bgr, hint=None):
        """(앵커 좌상단(x, y), 배율) 또는 None.

        hint=((x, y), scale): 직전 프레임의 앵커. 창은 프레임 사이에 거의 안 움직이므로
        그 주변 소영역만 그 배율로 먼저 확인한다 — 전 프레임 다중 배율 탐색(수십 ms)을
        생략해 폴링 비용을 크게 줄인다 (2026-07-13 반응속도 개선). 빗나가면 전체 탐색.

        게이트를 배율 정련 **뒤에** 건다. 저배율에서는 임계값을 낮춘다
        (scaling.anchor_threshold) — 강제 21:9 의 실제 배율은 0.74 다.
        """
        cv2 = _cv2()
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        if hint is not None:
            found = self._check_hint(gray, hint)
            if found is not None:
                return found

        small = cv2.resize(gray, None, fx=_COARSE_SCALE, fy=_COARSE_SCALE,
                           interpolation=cv2.INTER_AREA)
        coarse = []
        for scale in scaling.scale_candidates(frame_bgr.shape[0]):
            _, template = self._anchor_at(scale)
            if (small.shape[0] < template.shape[0]
                    or small.shape[1] < template.shape[1]):
                continue
            result = cv2.matchTemplate(small, template, cv2.TM_CCOEFF_NORMED)
            # The main-window anchor can be partially covered by the confirm
            # dialog.  Keep separated runner-up peaks too; full-resolution
            # verification below remains the acceptance gate.
            for score, location in self._coarse_peaks(result, template.shape):
                coarse.append((score, scale, location))
        if not coarse:
            return None

        coarse.sort(key=lambda item: -item[0])
        margin = int(2 / _COARSE_SCALE) + 6
        best_location, best_score, best_scale = None, -1.0, None
        for _score, scale, location in coarse[:_PRECISE_CANDIDATES]:
            approx = (int(location[0] / _COARSE_SCALE),
                      int(location[1] / _COARSE_SCALE))
            found, score = self._score_near(gray, approx, margin, scale)
            if found is not None and score > best_score:
                best_location, best_score, best_scale = found, score, scale
        if best_location is None:
            return None

        location, score, scale = self._refine_scale(gray, best_location, best_scale)

        if score < scaling.anchor_threshold(0.80, scale):
            return None
        return location, scale

    _HINT_MARGIN = 24   # 힌트 주변 탐색 여유 (px) — 창 미세 이동·판정 떨림 흡수

    def _check_hint(self, gray, hint):
        """직전 앵커 주변 소영역을 그 배율로만 매칭. 임계 미달이면 None (전체 탐색으로)."""
        (hx, hy), scale = hint
        found = self._match_near(gray, (hx, hy), self._HINT_MARGIN, scale)
        return (found, scale) if found is not None else None


@dataclass
class MainReading:
    target: int = None            # 목표 단계 (예: 13)
    kind: str = None              # "armor" | "weapon"
    grade: str = None             # "t4_1590" | "t4_1730"
    growth_blocked: bool = False
    anchor: tuple = None
    scale: float = 1.0
    warnings: list = field(default_factory=list)

    def complete(self) -> bool:
        return (self.target is not None and self.kind is not None
                and self.grade is not None)


def _mask_green(bgr):
    cv2 = _cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (35, 70, 110), (85, 255, 255))


def _mask_count(bgr):
    """재료 수량은 충분하면 초록, 부족하면 빨강이다. 둘 다 받는다."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (35, 70, 110), (85, 255, 255))
    red_low = cv2.inRange(hsv, (0, 90, 110), (10, 255, 255))
    red_high = cv2.inRange(hsv, (170, 90, 110), (179, 255, 255))
    return cv2.bitwise_or(green, cv2.bitwise_or(red_low, red_high))


def _mask_orange(bgr):
    """주황 글씨 (확인 창 장인의 기운·책 보너스·현재 단계). 확인 판독기가 import 해 쓴다."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (8, 90, 110), (30, 255, 255))


def _mask_light(bgr):
    """흰색 + 노랑/금색을 함께 잡는다 (확인 창 성공률·숨결 카운트).

    glyphs.mask_bright_text 의 금색 범위(H 18~35)로는 확인 창의 노랑을 놓쳐
    "6.00%" 가 3개, "0 / 20" 이 1개 성분으로 잡힌다 (실측). 넓은 범위로 따로 둔다.
    """
    cv2 = _cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 150), (179, 60, 255))
    gold = cv2.inRange(hsv, (15, 80, 140), (40, 255, 255))
    return cv2.bitwise_or(white, gold)


def icon_kind(bgr):
    """아이콘 색조로 부위 판별. 파랑=armor, 빨강=weapon, 모르면 None (추측 금지).

    채도만 걸면 아이콘 테두리 광택이 섞여 빨강이 파랑으로 나온다. 명도(V>90)도 함께 건다.
    실측: 빙하/수호석 H=103~104, 용암/파괴석 H=8~10.
    """
    cv2 = _cv2()
    import numpy as np
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    strong = (hsv[..., 1] > 110) & (hsv[..., 2] > 90)
    if strong.sum() < 30:
        return None
    hue = float(np.median(hsv[..., 0][strong]))
    if 90 <= hue <= 130:
        return "armor"
    if hue <= 20 or hue >= 160:
        return "weapon"
    return None


def read_digits(mask, templates, scale, allowed=None):
    """마스크의 글리프를 왼→오로 읽어 문자열로. 하나라도 못 읽으면 None."""
    out = []
    for x, y, w, h in glyphs.glyph_components(mask, scale):
        char, _ = glyphs.match_digit(glyphs.normalize_glyph(mask[y:y + h, x:x + w]),
                                     templates, threshold=0.30, allowed=allowed)
        if char is None:
            return None
        out.append(char)
    return "".join(out) if out else None


def gap_split_counts(mask, templates, scale):
    """'보유 / 최대' 를 (보유, 최대) 로. 슬래시가 아니라 가장 큰 x-간격에서 가른다.

    슬래시 글리프는 배율 0.74 에서 크기 필터에 걸려 사라지므로, 슬래시 위치로 가를 수 없다.
    """
    comps = sorted(glyphs.glyph_components(mask, scale), key=lambda c: c[0])
    if len(comps) < 2:
        return None
    gaps = [(comps[i + 1][0] - (comps[i][0] + comps[i][2]), i) for i in range(len(comps) - 1)]
    _, split = max(gaps)
    result = []
    for side in (comps[:split + 1], comps[split + 1:]):
        text = ""
        for x, y, w, h in side:
            char, _ = glyphs.match_digit(glyphs.normalize_glyph(mask[y:y + h, x:x + w]),
                                         templates, threshold=0.30)
            if char == "/":
                continue
            if char is None or not char.isdigit():
                return None
            text += char
        if not text:
            return None
        result.append(int(text))
    return tuple(result)


class MainWindowReader(_AnchorReader):
    _anchor_file = "main_anchor.png"

    def __init__(self, data_dir=_DATA_DIR):
        super().__init__(data_dir)
        self._digits = None

    def digit_templates(self):
        if self._digits is None:
            self._digits = glyphs.load_digit_templates(
                os.path.join(self._data_dir, "digits"))
        return self._digits

    def _crop(self, bgr, anchor, scale, name):
        x, y = anchor
        x1, y1, x2, y2 = g.scaled(g.MAIN_REGIONS, scale)[name]
        return bgr[y + y1:y + y2, x + x1:x + x2]

    def read(self, frame_bgr):
        found = self.find_anchor(frame_bgr)
        if found is None:
            return None
        anchor, scale = found
        reading = MainReading(anchor=anchor, scale=scale)
        templates = self.digit_templates()

        target = read_digits(_mask_green(self._crop(frame_bgr, anchor, scale, "next_band")),
                             templates, scale, allowed=set("0123456789"))
        reading.target = int(target) if target and target.isdigit() else None
        if reading.target is None:
            reading.warnings.append("목표 단계 판독 실패")

        reading.kind = icon_kind(self._crop(frame_bgr, anchor, scale, "material_icon"))
        if reading.kind is None:
            reading.warnings.append("부위 판별 실패")

        reading.growth_blocked = _has_red_warning(frame_bgr, anchor, scale)

        counts = [gap_split_counts(_mask_count(self._crop(frame_bgr, anchor, scale, name)),
                                   templates, scale)
                  for name in ("mat1_count", "mat2_count", "mat3_count")]
        if reading.kind and reading.target and all(c is not None for c in counts):
            needs = tuple(c[1] for c in counts)
            for grade in nr.grades(reading.kind):
                row = nr.stage_row(reading.kind, grade, reading.target)
                keys = [k for k in row["amount"]
                        if k not in (nr.GOLD_KEY, nr.FRAGMENT_KEY)]
                if len(keys) == 3 and tuple(row["amount"][k] for k in keys) == needs:
                    reading.grade = grade
                    break
            if reading.grade is None:
                reading.warnings.append("등급 역조회 실패")
        else:
            reading.warnings.append("재료 수량 판독 실패")

        return reading


def _has_red_warning(frame_bgr, anchor, scale):
    """'장비 성장 완료 후, 재련할 수 있습니다' — 붉은 경고 글씨 존재 여부."""
    cv2 = _cv2()
    import numpy as np
    x, y = anchor
    x1, y1, x2, y2 = (round(v * scale) for v in g.MAIN_GROWTH_WARNING)
    patch = frame_bgr[y + y1:y + y2, x + x1:x + x2]
    if patch.size == 0:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (0, 120, 120), (8, 255, 255))
    red |= cv2.inRange(hsv, (172, 120, 120), (179, 255, 255))
    return float(np.count_nonzero(red)) / red.size > 0.01
