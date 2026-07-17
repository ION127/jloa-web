# -*- coding: utf-8 -*-
"""확인 창 판독기. 앵커는 "재련 성공률" 라벨.

**창 판정 순서 주의:** 메인 앵커("재련 비용")는 확인 창 배경에도 비쳐 conf 0.90 으로
잡힌다. 반대로 확인 앵커는 메인 창에서 conf ≤ 0.63 이다. 그러므로 확인 앵커를 먼저 본다.

성공률 두 숫자의 의미 (스펙 §2.2.1):
    현재 = base + 실패스택 + 책보너스 + 선택숨결 × 단가
    최대 = 현재 + (숨결최대 − 선택숨결) × 단가       ← 파생값. 새 정보 없음.

`최대` 로 base 를 역산할 수 없다 (숨결 슬라이더를 끝까지 민 '현재' 일 뿐이다).
그래서 base 는 화면에서 못 얻고, 등급 확정 후 normal_refine.json 에서 조회한다.
"""
import os
from dataclasses import dataclass, field

from app.features import normal_refine as nr
from app.vision import glyphs
from app.vision import normal_refine_geometry as g
from app.vision.normal_refine_window import (_AnchorReader, _DATA_DIR, _cv2,
                                             _mask_green, _mask_light, _mask_orange,
                                             gap_split_counts, icon_kind, read_digits)


# 부위 판별은 메인 창과 완전히 같은 규칙을 쓴다 (icon_kind). 확인 창에서는 숨결 아이콘,
# 메인 창에서는 첫 재료 아이콘이 대상일 뿐이다. 실측: 빙하 H=103~104, 용암 H=8~10.


@dataclass
class ConfirmReading:
    level: int = None
    target: int = None
    prob_current: float = None     # 화면의 '현재' (%). 책·숨결 보너스가 섞여 있다.
    prob_max: float = None         # 화면의 '최대' (%). 파생값 — base 를 못 준다.
    jangin: float = None
    breath_have: int = None
    breath_selected: int = None
    breath_max: int = None
    breath_per_unit: float = None  # 1개당 상승률 (%)
    book_bonus: float = 0.0        # 슬롯에 적힌 "재련 기본 성공률 +X%". 비었으면 0.
    gear_grade: str = None         # 장비 목록 선택 행에서 판별한 등급 ("t4_1590"/"t4_1730"/None)
    kind: str = None
    anchor: tuple = None
    scale: float = 1.0
    warnings: list = field(default_factory=list)

    @property
    def book_registered(self) -> bool:
        return self.book_bonus > 0.0

    def consistent(self) -> bool:
        """최대 == 현재 + (숨결최대 - 선택숨결) x 단가. 어긋나면 숫자 판독이 틀렸다."""
        if None in (self.prob_current, self.prob_max, self.breath_max,
                    self.breath_selected, self.breath_per_unit):
            return False
        derived = (self.prob_current
                   + (self.breath_max - self.breath_selected) * self.breath_per_unit)
        return abs(self.prob_max - derived) < 0.02

    def fail_stack(self, base: float) -> float:
        """실패스택 = 현재 - base - 책보너스 - 선택숨결 x 단가.

        base 는 화면에서 못 읽는다 (최대는 파생값이다). 호출자가 등급을 확정한 뒤
        normal_refine.json 에서 가져와 넘겨야 한다.
        """
        return (self.prob_current - base - self.book_bonus
                - self.breath_selected * self.breath_per_unit)

    def pure_prob(self, base: float) -> float:
        """recommend() 에 넘길 값 = base + 실패스택. 화면의 '현재' 가 아니다."""
        return base + max(self.fail_stack(base), 0.0)

    def complete(self) -> bool:
        return (self.level is not None and self.target is not None
                and self.prob_current is not None and self.prob_max is not None
                and self.jangin is not None and self.breath_max is not None
                and self.breath_per_unit is not None
                and self.breath_selected is not None
                and self.kind is not None
                and self.consistent())


class ConfirmWindowReader(_AnchorReader):
    _anchor_file = "confirm_anchor.png"

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
        x1, y1, x2, y2 = g.scaled(g.CONFIRM_REGIONS, scale)[name]
        return bgr[y + y1:y + y2, x + x1:x + x2]

    def _text(self, bgr, anchor, scale, name, reading, mask=None):
        patch = self._crop(bgr, anchor, scale, name)
        masked = (mask or glyphs.mask_bright_text)(patch)
        text = read_digits(masked, self.digit_templates(), scale)
        if text is None:
            reading.warnings.append(f"{name} 판독 실패")
        return text

    @staticmethod
    def _percent(text):
        """숫자만 읽고 **소수점 두 자리로 고정 해석**한다.

        소수점은 크기 필터에 걸려 글리프로 잡히지 않는다 (실측). 게임의 퍼센트는 항상 2자리다.
        "300%" -> 3.00,  "6279%" -> 62.79,  "006%" -> 0.06,  "3174%" -> 31.74
        """
        if not text:
            return None
        digits = "".join(ch for ch in text if ch.isdigit())
        return int(digits) / 100.0 if digits else None

    def _read_book_bonus(self, bgr, anchor, scale, reading):
        """슬롯의 "+3.00%" (주황). 비면 주황 픽셀이 없으므로 0.0.

        "특수 재료를 등록하세요." 는 회색이다. 밝기로 가르면 둘 다 글씨라 구별되지 않는다.
        실측: 비었음 주황비 0.00%, 책 등록 6.93%.
        """
        cv2 = _cv2()
        import numpy as np
        patch = self._crop(bgr, anchor, scale, "book_bonus")
        if patch.size == 0:
            return 0.0
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        orange = cv2.inRange(hsv, (10, 120, 140), (30, 255, 255))
        if float(np.count_nonzero(orange)) / orange.size < g.BOOK_SLOT_ORANGE_MIN:
            return 0.0
        value = self._percent(read_digits(orange, self.digit_templates(), scale))
        if value is None:   # 영역은 "3.00%" 만 담는다 ('+' 는 밖). 못 읽으면 판독 실패다.
            reading.warnings.append("책 보너스 판독 실패")
            return 0.0
        return value

    def _read_gear_grade(self, frame_bgr, anchor, scale):
        """장비 목록의 선택 행(파란 테두리)을 찾아 아이콘의 파란 장식 밀도로 등급 판별.

        운명의 전율(1730)은 아이콘에 파란 화염 장식이 있고(밀도 ≥0.155), 결단·업화(1590)는
        금색 프레임뿐이다(≤0.014). 실측 7장 전부 분리 — 임계 g.GEAR_BLUE_MIN.
        선택 행을 못 찾으면 None (추측 금지 — 숨결 판별·설정 폴백이 이어받는다).
        """
        cv2 = _cv2()
        import numpy as np
        ax, ay = anchor
        x1, y1, x2, y2 = (round(v * scale) for v in g.GEAR_LIST_BOX)
        crop = frame_bgr[max(0, ay + y1):ay + y2, max(0, ax + x1):ax + x2]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, (90, 50, 40), (125, 255, 255))
        ys, xs = np.where(blue > 0)
        if len(xs) < 50:
            return None
        # 파랑이 가장 밀집한 세로 대역 = 선택 행 (테두리가 행 전체를 두른다)
        histogram = np.bincount(ys, minlength=crop.shape[0])
        window = max(int(50 * scale), 1)
        center = int(np.argmax(np.convolve(histogram, np.ones(window), "same")))
        band = (ys > center - 45 * scale) & (ys < center + 45 * scale)
        if not band.any():
            return None
        by1, by2 = ys[band].min(), ys[band].max()
        bx1 = xs[band].min()
        height = by2 - by1
        if height < 20 * scale:
            return None
        icon = crop[by1 + 3:by2 - 3, bx1 + 3:bx1 + 3 + max(height - 6, 10)]
        if icon.size == 0:
            return None
        icon_hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
        density = cv2.inRange(icon_hsv, (90, 60, 50), (125, 255, 255)).mean() / 255
        return "t4_1730" if density >= g.GEAR_BLUE_MIN else "t4_1590"

    def read(self, frame_bgr, hint=None):
        found = self.find_anchor(frame_bgr, hint=hint)
        if found is None:
            return None
        anchor, scale = found
        reading = ConfirmReading(anchor=anchor, scale=scale)

        level = self._text(frame_bgr, anchor, scale, "level_band", reading, _mask_orange)
        target = self._text(frame_bgr, anchor, scale, "next_band", reading, _mask_green)
        reading.level = int(level) if level and level.isdigit() else None
        reading.target = int(target) if target and target.isdigit() else None

        reading.prob_current = self._percent(
            self._text(frame_bgr, anchor, scale, "prob_current", reading, _mask_light))
        reading.prob_max = self._percent(
            self._text(frame_bgr, anchor, scale, "prob_max", reading, _mask_light))
        reading.jangin = self._percent(
            self._text(frame_bgr, anchor, scale, "jangin_cur", reading, _mask_orange))
        reading.breath_per_unit = self._percent(
            self._text(frame_bgr, anchor, scale, "per_unit", reading, _mask_light))

        have = self._text(frame_bgr, anchor, scale, "breath_have", reading, _mask_light)
        reading.breath_have = int(have) if have and have.isdigit() else None

        # 슬래시는 배율 0.74 에서 사라지므로 '/' 로 가르지 않는다. 가장 큰 x-간격에서 (선택, 최대)로 가른다.
        sel_max = gap_split_counts(
            _mask_light(self._crop(frame_bgr, anchor, scale, "breath_sel_max")),
            self.digit_templates(), scale)
        if sel_max is not None:
            reading.breath_selected, reading.breath_max = sel_max
        else:
            reading.warnings.append("숨결 선택/최대 판독 실패")

        reading.book_bonus = self._read_book_bonus(frame_bgr, anchor, scale, reading)

        reading.kind = icon_kind(self._crop(frame_bgr, anchor, scale, "breath_icon"))
        if reading.kind is None:
            reading.warnings.append("숨결 아이콘으로 부위 판별 실패")

        reading.gear_grade = self._read_gear_grade(frame_bgr, anchor, scale)

        if not reading.consistent():
            reading.warnings.append("성공률 정합성 실패 (최대 != 현재 + 남은숨결 x 단가)")

        return reading
