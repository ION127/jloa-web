"""상급재련 창 판독기 — 캡처 프레임에서 재련 상태를 읽는다.

판독 항목 (refine_geometry 실측 규칙):
  단계(level) · 진행바(bar 0~90) → 밴드 exp = (단계%10)×100 + bar
  선조의 가호 stack(소켓 점등 0/2/4/6) · 나베르(enh_next, 골드 텍스트)
  숨결/책 체크 상태 · 부위(armor/weapon, 첫 재료 색)

cv2/numpy는 lazy import (게임 감지 전 비용 0 — 젬 판독기와 동일 원칙).
"""

import json
import os
from dataclasses import dataclass, field

from app.vision import refine_geometry, scaling
from app.vision.glyphs import (
    digit_components, match_digit, normalize_glyph,
)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "vision", "refine")

_COARSE_SCALE = 0.25       # 1차 탐색 축소 배율
_COARSE_THRESHOLD = 0.60   # 축소본은 흐려져 점수가 낮다 (정밀 단계에서 재검증)
_PRECISE_CANDIDATES = 6    # 축소본 상위 N개만 원본 해상도로 재검증


def _cv2():
    import cv2
    return cv2


@dataclass
class RefineReading:
    level: int = None          # 현재 단계 (예: 23)
    bar: int = None            # 단계 내 진행도 0~90 (10 단위)
    stack: int = None          # 선조의 가호 0/2/4/6
    enh_next: bool = False     # 나베르의 송곳 활성
    breath_checked: bool = False
    book_checked: bool = False
    equip_type: str = None     # "armor" | "weapon"
    anchor: tuple = None       # 프레임 좌표 앵커 (x, y)
    scale: float = 1.0
    warnings: list = field(default_factory=list)

    @property
    def exp(self):
        """밴드(0~1000) 내 exp. 단계%10 == 0 이면 밴드 시작."""
        if self.level is None or self.bar is None:
            return None
        return (self.level % 10) * 100 + self.bar

    @property
    def band(self):
        """t4_0..t4_3 (10단계 구간). 40단계 이상(완료)은 None."""
        if self.level is None:
            return None
        index = self.level // 10
        return f"t4_{index}" if 0 <= index <= 3 else None

    def complete(self) -> bool:
        return (self.level is not None and self.bar is not None
                and self.stack is not None and self.equip_type is not None
                and self.band is not None)


class RefineWindowReader:
    def __init__(self, data_dir: str = _DATA_DIR):
        with open(os.path.join(data_dir, "manifest.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)
        self._data_dir = data_dir
        self._anchor_gray = None
        self._anchor_scaled = {}
        self._digit_templates = None
        self.scale = 1.0

    # ---------- 자산 로드 ----------

    def _imread(self, path):
        import numpy as np
        return _cv2().imdecode(np.fromfile(path, np.uint8), _cv2().IMREAD_GRAYSCALE)

    @property
    def anchor_gray(self):
        if self._anchor_gray is None:
            self._anchor_gray = self._imread(os.path.join(self._data_dir, "anchor.png"))
        return self._anchor_gray

    @property
    def digit_templates(self):
        if self._digit_templates is None:
            self._digit_templates = {}
            for label, files in self.manifest["templates"]["digits"].items():
                self._digit_templates[label] = [
                    self._imread(os.path.join(self._data_dir, "digits", f))
                    for f in files]
        return self._digit_templates

    # ---------- 앵커 ----------

    def _anchor_at(self, scale: float):
        if scale == 1.0:
            return self.anchor_gray
        cached = self._anchor_scaled.get(scale)
        if cached is None:
            cv2 = _cv2()
            h, w = self.anchor_gray.shape
            cached = cv2.resize(self.anchor_gray,
                                (max(1, round(w * scale)), max(1, round(h * scale))),
                                interpolation=cv2.INTER_AREA)
            self._anchor_scaled[scale] = cached
        return cached

    def scale_candidates(self, frame_height: int):
        """UI 배율 후보 — 젬·재련 공통(app.vision.scaling).

        네이티브(FHD/QHD/4K·울트라와이드)뿐 아니라 로아 강제 화면비의 레터박스
        배율(0.5·0.76×)까지 포함한다. 근거는 scaling.py 참고.
        """
        return scaling.scale_candidates(frame_height)

    def _score_near(self, gray, top_left, margin, scale):
        """앵커 주변 정밀 매칭 — 임계 게이트 없이 (위치, 점수). 배율 정련용."""
        cv2 = _cv2()
        template = self._anchor_at(scale)
        th, tw = template.shape
        x, y = top_left
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1 = min(gray.shape[1], x + tw + margin)
        y1 = min(gray.shape[0], y + th + margin)
        region = gray[y0:y1, x0:x1]
        if region.shape[0] < th or region.shape[1] < tw:
            return None, -1.0
        result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, conf, _, loc = cv2.minMaxLoc(result)
        return (x0 + loc[0], y0 + loc[1]), conf

    def _threshold(self, scale):
        return scaling.anchor_threshold(
            self.manifest.get("anchor_threshold", 0.80), scale)

    def _match_near(self, gray, top_left, margin, scale):
        """앵커 후보 위치 주변만 정밀 매칭 (전체 탐색보다 훨씬 싸다)."""
        loc, conf = self._score_near(gray, top_left, margin, scale)
        if loc is None or conf < self._threshold(scale):
            return None, max(conf, 0.0)
        return loc, conf

    def _refine_scale(self, gray, loc, scale0):
        """격자 배율 주변을 연속 정련해 실제 UI 배율을 찾는다 → (위치, 점수, 배율)."""
        results = []
        for scale in scaling.fine_scale_candidates(scale0):
            found, conf = self._score_near(gray, loc, 10, scale)
            if found is not None:
                results.append((conf, scale, found))
        if not results:
            return loc, -1.0, scale0
        conf, scale, found = scaling.pick_refined(results)
        return found, conf, scale

    def find_anchor(self, bgr, hint=None):
        """계층적 + 다중 배율 탐색 → (x, y) 또는 None. 배율은 self.scale에 저장.

        1) 직전 앵커 위치(hint)와 배율이 맞으면 근방만 보고 끝
        2) 아니면 1/4 축소본으로 배율·대략 위치를 훑고
        3) 상위 후보만 원본 해상도로 정밀 재검증해 **최고 점수 배율**을 확정한다.
           (축소본 점수는 이웃 배율끼리 비슷해 성급히 고르면 좌표가 통째로 어긋난다)
        """
        cv2 = _cv2()
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        if hint is not None:
            loc, _conf = self._match_near(gray, hint, 12, self.scale)
            if loc is not None:
                return loc

        small = cv2.resize(gray, None, fx=_COARSE_SCALE, fy=_COARSE_SCALE,
                           interpolation=cv2.INTER_AREA)
        coarse = []
        for scale in self.scale_candidates(bgr.shape[0]):
            template = self._anchor_at(scale)
            tmpl_small = cv2.resize(template, None, fx=_COARSE_SCALE, fy=_COARSE_SCALE,
                                    interpolation=cv2.INTER_AREA)
            if (small.shape[0] < tmpl_small.shape[0]
                    or small.shape[1] < tmpl_small.shape[1]):
                continue
            result = cv2.matchTemplate(small, tmpl_small, cv2.TM_CCOEFF_NORMED)
            _, conf, _, loc = cv2.minMaxLoc(result)
            if conf >= _COARSE_THRESHOLD:
                coarse.append((conf, scale, loc))
        if not coarse:
            return None

        coarse.sort(key=lambda t: -t[0])
        margin = int(2 / _COARSE_SCALE) + 6
        best_loc, best_conf, best_scale = None, -1.0, None
        for _conf, scale, loc in coarse[:_PRECISE_CANDIDATES]:
            approx = (int(loc[0] / _COARSE_SCALE), int(loc[1] / _COARSE_SCALE))
            # 게이트 없이 점수만 — 격자 배율은 진짜 배율을 빗나갈 수 있어
            # 여기서 걸러 버리면 정련 기회를 잃는다.
            found, precise = self._score_near(gray, approx, margin, scale)
            if found is not None and precise > best_conf:
                best_loc, best_conf, best_scale = found, precise, scale
        if best_loc is None:
            return None

        loc, conf, scale = self._refine_scale(gray, best_loc, best_scale)
        if conf < self._threshold(scale):
            return None
        self.scale = scale
        return loc

    # ---------- 필드 판독 ----------

    def _regions(self):
        return refine_geometry.scaled(refine_geometry.REGIONS, self.scale)

    def _crop(self, bgr, anchor, region_name):
        ax, ay = anchor
        x1, y1, x2, y2 = self._regions()[region_name]
        return bgr[ay + y1:ay + y2, ax + x1:ax + x2]

    @staticmethod
    def _gold_mask(bgr):
        import numpy as np
        cv2 = _cv2()
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        return (((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
                 & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 150))
                .astype(np.uint8) * 255)

    def _read_level(self, bgr, anchor, reading):
        mask = self._gold_mask(self._crop(bgr, anchor, "level_band"))
        comps = [c for c in digit_components(mask, self.scale)
                 if c[2] <= round(11 * self.scale)]
        if not comps or len(comps) > 2:
            reading.warnings.append(f"level 글리프 {len(comps)}개")
            return
        text = ""
        for x, y, w, h in comps:
            label, score = match_digit(normalize_glyph(mask[y:y + h, x:x + w]),
                                       self.digit_templates)
            if label is None:
                reading.warnings.append(f"level 숫자 매칭 실패 ({score:.2f})")
                return
            text += label
        reading.level = int(text)

    def _read_bar(self, bgr, anchor, reading):
        import numpy as np
        cv2 = _cv2()
        band = self._crop(bgr, anchor, "bar_band")
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        bright = (hsv[:, :, 2] > 90).mean(axis=0)
        cols = np.where(bright > 0.5)[0]
        if len(cols) == 0:
            reading.warnings.append("bar 채움 미검출")
            return
        end = cols[0]
        for c in cols:              # 왼쪽부터 연속 구간의 끝
            if c - end <= round(6 * self.scale):
                end = c
            else:
                break
        x1 = self._regions()["bar_band"][0]
        zero = round(refine_geometry.BAR_ZERO_X * self.scale) - x1
        per_pct = refine_geometry.BAR_PX_PER_PCT * self.scale
        pct = (end - zero) / per_pct
        bar = max(0, min(90, round(pct / 10) * 10))   # 진행도는 10 단위
        if abs(pct - bar) > 4:
            reading.warnings.append(f"bar {pct:.1f}% — 10단위서 벗어남")
        reading.bar = bar

    def _read_sockets(self, bgr, anchor, reading):
        cv2 = _cv2()
        ax, ay = anchor
        radius = max(3, round(5 * self.scale))
        lit = 0
        for cx, cy in refine_geometry.scaled_points(refine_geometry.SOCKETS, self.scale):
            patch = bgr[ay + cy - radius:ay + cy + radius,
                        ax + cx - radius:ax + cx + radius]
            if patch.size == 0:
                reading.warnings.append("socket 범위 밖")
                return
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            orange = ((hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 90)
                      & (hsv[:, :, 2] > 130)).mean()
            bright = (hsv[:, :, 2] > 170).mean()
            if max(orange, bright) > 0.5:
                lit += 1
        reading.stack = lit

    def _read_naber(self, bgr, anchor, reading):
        mask = self._gold_mask(self._crop(bgr, anchor, "naber_band"))
        # bool()로 감싸지 않으면 np.bool_이 새어나가 `is False` 비교가 어긋난다
        reading.enh_next = bool((mask > 0).mean() > 0.05)

    def _read_checkbox(self, bgr, anchor, region_name):
        cv2 = _cv2()
        box = self._crop(bgr, anchor, region_name)
        gray = cv2.cvtColor(box, cv2.COLOR_BGR2GRAY)
        return float((gray > 150).mean()) > 0.08

    def _read_equip(self, bgr, anchor, reading):
        cv2 = _cv2()
        icon = self._crop(bgr, anchor, "material_icon")
        hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1] > 100
        hue = hsv[:, :, 0]
        blue = int(((hue > 90) & (hue < 130) & sat).sum())
        red = int((((hue < 12) | (hue > 168)) & sat).sum())
        if blue + red < 50:
            reading.warnings.append("재료 아이콘 색 미검출")
            return
        reading.equip_type = "armor" if blue > red else "weapon"

    # ---------- 종합 ----------

    def read(self, frame_bgr, hint=None) -> RefineReading:
        anchor = self.find_anchor(frame_bgr, hint=hint)
        if anchor is None:
            return None
        reading = RefineReading(anchor=anchor, scale=self.scale)
        self._read_level(frame_bgr, anchor, reading)
        self._read_bar(frame_bgr, anchor, reading)
        self._read_sockets(frame_bgr, anchor, reading)
        self._read_naber(frame_bgr, anchor, reading)
        reading.breath_checked = self._read_checkbox(frame_bgr, anchor, "checkbox_breath")
        reading.book_checked = self._read_checkbox(frame_bgr, anchor, "checkbox_book")
        self._read_equip(frame_bgr, anchor, reading)
        return reading
