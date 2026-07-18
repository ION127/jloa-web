"""젬 가공 창 판독 (Phase 6.1).

흐름: 앵커를 여러 배율로 찾아 창 위치와 **UI 배율**을 함께 확정 →
      배율 환산한 상대 좌표에서 각 영역 크롭 → 마스크(버튼/다이아/슬롯)
      → 글리프·텍스트 템플릿 매칭.

해상도 대응: 좌표·크기 기준은 1920×1080(UI 100%)이고, 다른 해상도는 배율 하나로 환산한다.
게임 UI는 세로 해상도에 비례해 커지고 가로는 여백만 늘어나므로 21:9·32:9도 같은 배율이다.
템플릿 매칭 자체는 글리프를 정규 상자로 리사이즈한 뒤 하므로 배율과 무관하다.

판독 실패는 조용히 넘기지 않는다: warnings에 남기고 미인식 크롭을
%APPDATA%/lostark_app/vision_unknown/ 에 저장해 템플릿을 늘릴 수 있게 한다.
사용자는 오버레이/패널에서 수동 보정할 수 있으므로 부분 인식도 유용하다.

cv2/numpy는 lazy import (이 기능을 쓰지 않는 실행에는 로드되지 않음 — 저사양 원칙).
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field

from app.vision import geometry, glyphs, scaling

log = logging.getLogger("vision.gem")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "vision", "gem")

def _valid_entry(entry_id):
    """엔진 효과 풀에 실제로 있는 id만 통과시킨다.

    숫자를 오판독하면 'points_p8'처럼 존재할 수 없는 id가 만들어져 엔진에서 KeyError로
    앱이 죽었다 (2026-07-09 실사용 크래시). 증감 폭은 +1~+4/-1, 보기 증가는 +1~+2뿐이다.
    """
    from app.features.gem_craft import POOL
    return entry_id if entry_id in POOL else None


_UNKNOWN_CAP = 300        # 미인식 크롭 폴더당 상한 (자동 감지가 초당 여러 번 폴하므로
                          # 상한이 없으면 판독 실패가 잦을 때 %APPDATA%를 수천 장으로 채운다)
_COARSE_SCALE = 0.25      # 1차 탐색 축소 배율
_COARSE_THRESHOLD = 0.60  # 축소본은 흐려져 점수가 낮게 나온다 (정밀 단계에서 재검증)

_PRECISE_CANDIDATES = 6   # 축소본 점수 상위 N개만 원본 해상도로 재검증
                          # (레터박스 배율까지 후보가 늘어 4 → 6)

_POINT_NAMES = ("혼돈 포인트", "질서 포인트")
_WILLPOWER_NAME = "의지력 효율"
_REROLL_NAME = "다른 항목 보기"
_COST_NAME = "가공 비용"
_KEEP_NAME = "가공 상태"


@dataclass
class GemReading:
    willpower: int = None
    points: int = None
    effect1: int = None           # 좌측 다이아
    effect2: int = None           # 우측 다이아
    gem_point: int = None         # 표기 합 (교차검증용)
    attempts: int = None
    attempts_max: int = None
    rerolls: int = None
    reset_available: bool = None  # 초기화 버튼 활성 여부
    resets: int = None            # "초기화 (N/M)"의 N — 남은 초기화 횟수
    resets_max: int = None        # 같은 문자열의 M
    shown_ids: list = field(default_factory=list)
    shown_labels: list = field(default_factory=list)
    buttons: dict = field(default_factory=dict)   # 이름 → 프레임 절대 (x1,y1,x2,y2)
    enabled: dict = field(default_factory=dict)   # 이름 → 활성 여부
    anchor: tuple = None
    confidence: float = 0.0
    scale: float = 1.0            # 이 프레임의 UI 배율 (1080p 기준)
    warnings: list = field(default_factory=list)

    def grade(self):
        return {5: "고급", 7: "희귀", 9: "영웅"}.get(self.attempts_max)

    def complete(self) -> bool:
        """추천을 계산해도 되는 상태인가.

        - 제시 4개 중 하나라도 못 읽으면 가공 기대값이 3개 평균으로 계산돼 틀린다
        - 젬 포인트 표기와 4스탯 합이 다르면 어딘가를 오판독한 것이다 (교차검증)
        틀린 추천보다 "못 읽었습니다"가 낫다.
        """
        return (all(v is not None for v in (
            self.willpower, self.points, self.effect1, self.effect2,
            self.attempts, self.rerolls))
            and len(self.shown_ids) == 4 and all(self.shown_ids)
            and self.stats_consistent())

    def stats_consistent(self) -> bool:
        """젬 포인트 표기 == 4스탯 합 (판독 오류 자동 검출)."""
        if self.gem_point is None or not all(
                v is not None for v in (self.willpower, self.points, self.effect1, self.effect2)):
            return True
        return self.gem_point == self.willpower + self.points + self.effect1 + self.effect2


class GemWindowReader:
    def __init__(self, data_dir: str = _DATA_DIR, unknown_dir: str = None):
        import cv2
        import numpy as np
        self._cv2, self._np = cv2, np
        with open(os.path.join(data_dir, "manifest.json"), encoding="utf-8") as f:
            self.meta = json.load(f)
        # 기준 좌표(1920×1080). 다른 해상도에선 set_scale()이 환산본을 만들어 둔다.
        self.base_regions = self.meta["regions"]
        self.base_buttons = self.meta["buttons"]
        self.regions = dict(self.base_regions)
        self.button_boxes = dict(self.base_buttons)
        self.scale = 1.0
        self.anchor_threshold = self.meta.get("anchor_threshold", 0.80)
        self._anchor = self._load(os.path.join(data_dir, "anchor.png"))
        self._anchor_cache = {}
        self._digits = {
            label: [self._load(os.path.join(data_dir, "digits", f)) for f in files]
            for label, files in self.meta["templates"]["digits"].items()
        }
        self._names = self._load_group(data_dir, "names")
        self._cost_words = (self._load_group(data_dir, "costwords")
                            if "costwords" in self.meta["templates"] else {})
        if unknown_dir is None:
            from app.config import CONFIG_DIR
            unknown_dir = os.path.join(CONFIG_DIR, "vision_unknown")
        self._unknown_dir = unknown_dir
        self._unknown_saved = None   # 상한 판단용 폴더 파일 수 (첫 저장 때 lazy 카운트)

    def set_scale(self, scale: float):
        """UI 배율 확정 — 판독 영역·버튼 좌표를 그 배율로 환산해 둔다."""
        if scale == self.scale:
            return
        self.scale = scale
        self.regions = geometry.scaled(self.base_regions, scale)
        self.button_boxes = geometry.scaled(self.base_buttons, scale)

    def _load(self, path):
        return self._cv2.imdecode(self._np.fromfile(path, self._np.uint8),
                                  self._cv2.IMREAD_GRAYSCALE)

    def _load_group(self, data_dir, group):
        """{라벨: [템플릿, ...]} — manifest 값은 파일명 하나 또는 배율별 변형 목록."""
        out = {}
        for label, files in self.meta["templates"][group].items():
            if isinstance(files, str):
                files = [files]
            out[label] = [self._load(os.path.join(data_dir, group, f)) for f in files]
        return out

    # ---------- 프레임 유틸 ----------

    def _crop(self, bgr, anchor, region_name):
        ax, ay = anchor
        x1, y1, x2, y2 = self.regions[region_name]
        return bgr[ay + y1:ay + y2, ax + x1:ax + x2]

    def _gem_window_rect(self, anchor, shape):
        """젬 창 대략 영역 (명도 판단용). 앵커 기준 스탯 다이아 주변."""
        ax, ay = anchor
        s = self.scale
        x1 = max(0, ax - round(160 * s))
        y1 = max(0, ay - round(320 * s))
        x2 = min(shape[1], ax + round(320 * s))
        y2 = min(shape[0], ay + round(260 * s))
        return (x1, y1, x2, y2)

    def _save_unknown(self, mask, tag, color_crop=None):
        """미인식 크롭 저장 — 원본(색)도 함께 남긴다.

        마스크만 남기면 '전부 검은 마스크'라는 것만 알 뿐 왜 비었는지(색 범위 밖인지,
        너무 어두운지) 알 수 없다. 원본이 있으면 바로 진단된다 (2026-07-09 교훈).
        """
        try:
            if self._unknown_saved is None:   # 세션 첫 저장 때 기존 파일 수를 한 번 센다
                self._unknown_saved = (len(os.listdir(self._unknown_dir))
                                       if os.path.isdir(self._unknown_dir) else 0)
            if self._unknown_saved >= _UNKNOWN_CAP:
                return   # 상한 도달 — 더 쌓지 않는다 (수집 목적엔 이미 충분)
            os.makedirs(self._unknown_dir, exist_ok=True)
            stamp = int(time.time() * 1000)
            for suffix, image in (("mask", mask), ("raw", color_crop)):
                if image is None:
                    continue
                path = os.path.join(self._unknown_dir, f"{tag}_{stamp}_{suffix}.png")
                ok, encoded = self._cv2.imencode(".png", image)
                if ok:
                    encoded.tofile(path)
                    self._unknown_saved += 1
        except Exception:
            log.exception("미인식 크롭 저장 실패")

    def _anchor_at(self, scale: float):
        """현재 배율의 앵커 템플릿 (원본/축소본) — 캐시한다."""
        cached = self._anchor_cache.get(scale)
        if cached is None:
            cv2 = self._cv2
            if scale == 1.0:
                full = self._anchor
            else:
                full = cv2.resize(self._anchor, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_AREA if scale < 1
                                  else cv2.INTER_CUBIC)
            small = cv2.resize(full, None, fx=_COARSE_SCALE, fy=_COARSE_SCALE,
                               interpolation=cv2.INTER_AREA)
            cached = (full, small)
            self._anchor_cache[scale] = cached
        return cached

    def _score_near(self, gray, top_left, margin, scale):
        """앵커 후보 위치 주변 정밀 매칭 — 임계 게이트 없이 (위치, 점수). 배율 정련용."""
        cv2 = self._cv2
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
        _, conf, _, loc = cv2.minMaxLoc(result)
        return (x0 + loc[0], y0 + loc[1]), conf

    def _match_near(self, gray, top_left, margin, scale):
        """앵커 후보 위치 주변만 정밀 매칭 (전체 탐색보다 ~150배 싸다)."""
        loc, conf = self._score_near(gray, top_left, margin, scale)
        if loc is None or conf < scaling.anchor_threshold(self.anchor_threshold, scale):
            return None, max(conf, 0.0)
        return loc, conf

    def _refine_scale(self, gray, loc, scale0):
        """격자 배율 주변을 연속 정련해 실제 UI 배율을 찾는다 → (위치, 점수, 배율).

        후보 격자는 프레임 높이에서 나오지만 진짜 배율은 게임 내부 렌더가 정한다
        (scaling.fine_scale_candidates 주석 참고). 정련 없이는 창 높이가 몇 px만
        달라져도 앵커가 기각된다.
        """
        results = []
        for scale in scaling.fine_scale_candidates(scale0):
            found, conf = self._score_near(gray, loc, 10, scale)
            if found is not None:
                results.append((conf, scale, found))
        if not results:
            return loc, -1.0, scale0
        conf, scale, found = scaling.pick_refined(results)
        return found, conf, scale

    def scale_candidates(self, frame_height: int):
        """이 프레임에서 시도할 UI 배율 후보 — 젬·재련 공통(app.vision.scaling).

        네이티브(FHD/QHD/4K·울트라와이드)뿐 아니라 로아 강제 화면비의 레터박스
        배율(0.5·0.76×)까지 포함한다. 근거는 scaling.py 참고.
        """
        return scaling.scale_candidates(frame_height)

    def find_anchor(self, bgr, hint=None):
        """계층적 + 다중 배율 탐색. 반환: (위치, 신뢰도) — 배율은 self.scale에 저장.

        1) 직전 프레임의 앵커 위치(hint)와 배율이 맞으면 0.2ms로 끝
        2) 아니면 1/4 축소본에서 배율 후보를 훑어 대략 위치·배율을 잡고
        3) 그 주변만 원본 해상도로 정밀 확인
        """
        cv2 = self._cv2
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if hint is not None:
            loc, conf = self._match_near(gray, hint, 12, self.scale)
            if loc is not None:
                return loc, conf

        small = cv2.resize(gray, None, fx=_COARSE_SCALE, fy=_COARSE_SCALE,
                           interpolation=cv2.INTER_AREA)
        coarse = []
        for scale in self.scale_candidates(bgr.shape[0]):
            _, template = self._anchor_at(scale)
            if (small.shape[0] < template.shape[0]
                    or small.shape[1] < template.shape[1]):
                continue
            result = cv2.matchTemplate(small, template, cv2.TM_CCOEFF_NORMED)
            _, conf, _, loc = cv2.minMaxLoc(result)
            if conf >= _COARSE_THRESHOLD:
                coarse.append((conf, scale, loc))
        if not coarse:
            return None, 0.0

        # 축소본 점수는 이웃 배율끼리 비슷하게 나온다 → **원본 해상도 정밀 점수로 판정**한다.
        # (coarse 점수로 성급히 확정하면 1.05배 같은 이웃 배율을 잡아 좌표가 전부 어긋난다)
        coarse.sort(reverse=True)
        margin = int(2 / _COARSE_SCALE) + 6
        best = (None, -1.0, None)   # (loc, precise_conf, scale)
        for _conf, scale, loc in coarse[:_PRECISE_CANDIDATES]:
            approx = (int(loc[0] / _COARSE_SCALE), int(loc[1] / _COARSE_SCALE))
            # 게이트 없이 점수만 본다 — 격자 배율은 진짜 배율을 빗나가 점수가 낮게
            # 나올 수 있으므로, 여기서 걸러 버리면 정련 기회를 잃는다.
            found, precise = self._score_near(gray, approx, margin, scale)
            if found is not None and precise > best[1]:
                best = (found, precise, scale)
        if best[0] is None:
            return None, coarse[0][0]

        loc, conf, scale = self._refine_scale(gray, best[0], best[2])
        if conf < scaling.anchor_threshold(self.anchor_threshold, scale):
            return None, conf
        self.set_scale(scale)
        return loc, conf

    # ---------- 개별 판독 ----------

    def _match_digit_in_mask(self, mask, mode, allowed):
        if mode == "digit_last":
            return self._best_last_digit(mask, allowed=allowed)
        glyph = glyphs.extract_digit(mask, mode, self.scale)
        return glyphs.match_digit(glyph, self._digits, allowed=allowed)

    def _read_digit(self, bgr, anchor, region, mask_fn, mode, reading, allowed=None):
        crop = self._crop(bgr, anchor, region)
        mask = mask_fn(crop)
        label, score = self._match_digit_in_mask(mask, mode, allowed)
        if label is None and mask_fn is glyphs.mask_bright_text:
            # 파란 배경 젬은 금색 글자가 어두워 기본 마스크가 부서진다 — 완화 재시도
            mask = glyphs.mask_bright_text(crop, dim=True)
            label, score = self._match_digit_in_mask(mask, mode, allowed)
        if label is None:
            reading.warnings.append(f"{region} 숫자 판독 실패 (score={score:.2f})")
            self._save_unknown(mask, region, color_crop=crop)
            return None
        return int(label)

    def _read_quiet_digit(self, bgr, anchor, region, allowed=None):
        """보조 필드용 숫자 판독 — 실패해도 warnings/미인식 저장을 남기지 않는다."""
        if region not in self.regions:
            return None      # 구버전 manifest (regions에 없음) → 폴백
        mask = glyphs.mask_button_text(self._crop(bgr, anchor, region))
        glyph = glyphs.extract_digit(mask, False, self.scale)
        label, _score = glyphs.match_digit(glyph, self._digits, allowed=allowed)
        return int(label) if label is not None else None

    def _cost_direction(self, value_mask):
        """가공 비용의 증가/감소 판별.

        비용은 증감 모두 흰색이라 색으로 못 가르고, '+'/'-' 기호는 고배율에서 판별이
        무너진다 (실측). 값 오른쪽 끝 어절(증가/감소)을 정규화 매칭으로 가른다.
        어절 템플릿이 없으면 '+' 글리프 유무로 폴백.
        """
        if self._cost_words:
            word = glyphs.rightmost_word(value_mask, self.scale)
            if word is not None and word.size:
                label, score = glyphs.match_text(word, self._cost_words, threshold=0.5)
                if label == "증가":
                    return "cost_up"
                if label == "감소":
                    return "cost_down"
        return "cost_up" if glyphs.has_plus_sign(value_mask, self.scale) else "cost_down"

    def _best_last_digit(self, mask, threshold=0.55, x_max=None, allowed=None):
        """모든 글리프를 숫자로 매칭해 **최고 점수** 숫자를 채택 (임계 이상일 때).

        'L','v','.'·다이아 광택·한글 자모는 숫자 템플릿에 낮은 점수만 주므로
        진짜 숫자가 최고점이 된다. '오른쪽부터 첫 임계 통과' 방식은 Lv 배지가
        밀리는 젬에서 영역을 넓히자 우측 장식 무늬(0.55~0.6)가 진짜 숫자보다
        먼저 걸렸다 (2026-07-18 실측: Lv.1 을 '2'로 오판 → 교차검증 불일치).
        x_max: 그보다 오른쪽(증가/감소 어절)은 무시.
        allowed: 이 필드에 나올 수 있는 숫자만 후보로.
        """
        comps = glyphs.digit_components(mask, self.scale)
        if x_max is not None:
            comps = [c for c in comps if c[0] + c[2] / 2 <= x_max]
        best = (None, 0.0)
        for x, y, w, h in comps:
            glyph = glyphs.normalize_glyph(mask[y:y + h, x:x + w])
            label, score = glyphs.match_digit(glyph, self._digits, allowed=allowed)
            if score > best[1]:
                best = (label, score)
        if best[0] is not None and best[1] >= threshold:
            return best
        return (None, best[1])

    def _read_level(self, bgr, anchor, region, mode, reading):
        # 스탯 레벨은 1~5뿐 (gempago allowedDigits 방식)
        value = self._read_digit(bgr, anchor, region, glyphs.mask_bright_text,
                                 mode, reading, allowed=set("12345"))
        if value is not None and not 1 <= value <= 5:
            reading.warnings.append(f"{region} 레벨 범위 이탈: {value}")
            return None
        return value

    def _read_slot(self, bgr, anchor, slot, reading):
        """제시 슬롯 하나 → (엔진 entry id, 사람이 읽을 텍스트).

        어떤 스탯인지는 슬롯 아이콘 다이아의 **색**으로 안다 (glyphs.slot_stat).
        증가/감소는 값 텍스트의 **색**(초록/빨강), 수치는 숫자 글리프로 읽는다.
        문자열 통짜 매칭은 "+1 증가"와 "+3 증가"를 구분하지 못했다 (실측).

        무채색 슬롯(다른 항목 보기 / 가공 비용 / 가공 상태)만 이름 템플릿으로 가른다:
          다른 항목 보기 → "N회 증가"   (숫자 = 첫 글리프)
          가공 비용      → "±100% ..."  ('+' 글리프 유무로 증감)
          가공 상태      → "유지"
        """
        name_crop = self._crop(bgr, anchor, f"slot{slot}_name")
        value_crop = self._crop(bgr, anchor, f"slot{slot}_value")
        name_mask = glyphs.mask_slot_text(name_crop)
        value_mask = glyphs.mask_slot_text(value_crop)
        stat = glyphs.slot_stat(name_crop, scale=self.scale)
        color = glyphs.text_color(value_crop, value_mask)

        def digit(mode, allowed):
            """값 텍스트의 숫자.

            mode="first": "+N 증가" / "N회 증가" — 숫자가 맨 앞이다. 기호('+')는
                digit_glyph_candidates가 상대 높이로 이미 걸러 첫 후보가 곧 숫자.
            mode="gap":   "Lv. N 증가" — 'L','v' 뒤 숫자. 기하 규칙(최대 공백 직전)은
                저배율에서 한글 컴포넌트가 사라지면 'v'를 집는다 → 후보 중 허용 집합에서
                **최고 점수**를 고른다 ('L' 등 비숫자는 숫자 템플릿과 낮게 맞는다).
            """
            cands = glyphs.digit_glyph_candidates(value_mask, self.scale)
            if not cands:
                return None
            if mode == "first":
                label, _ = glyphs.match_digit(cands[0], self._digits, allowed=allowed)
                return label
            best_label, best_score = None, -1.0
            for glyph in cands:
                label, score = glyphs.match_digit(glyph, self._digits, allowed=allowed)
                if label is not None and score > best_score:
                    best_label, best_score = label, score
            return best_label

        entry_id, name = None, None
        if stat in ("willpower", "points"):
            if color == "red":
                # 감소는 공시 풀에서 -1뿐 — 숫자를 읽지 않는다 ('-'가 '1'에 붙어
                # 글리프가 오염되면 매칭이 실패했다, 2026-07-18 실측)
                entry_id = f"{stat}_m1"
            elif color == "green":
                # 스탯 증가폭: +1~+4 (allowedDigits)
                value = digit("first", set("1234"))
                if value:
                    entry_id = f"{stat}_p{value}"
        elif stat in ("effect1", "effect2"):
            side = stat[-1]
            if color == "white":
                entry_id = f"change{side}"    # "효과 변경" (흰 글씨)
            elif color == "red":
                entry_id = f"effect{side}_m1"     # 감소는 -1뿐 (위와 동일)
            elif color == "green":
                # 효과 레벨 증가폭: Lv+1~+4
                value = digit("gap", set("1234"))
                if value:
                    entry_id = f"effect{side}_p{value}"
        else:
            name, _score = glyphs.match_text(name_mask, self._names)
            if name == _REROLL_NAME:
                value = digit("first", set("12"))   # 보기 증가 +1~+2
                if value:
                    entry_id = f"reroll_p{value}"
            elif name == _COST_NAME:
                entry_id = self._cost_direction(value_mask)
            elif name == _KEEP_NAME:
                entry_id = "keep"

        raw_id, entry_id = entry_id, _valid_entry(entry_id)
        text = f"{name or stat or '?'} {color or '?'}"
        if entry_id is None:
            reason = f"{text} (id={raw_id})" if raw_id else text
            reading.warnings.append(f"슬롯{slot + 1} 미인식: {reason}")
            self._save_unknown(name_mask, f"slot{slot}_name", color_crop=name_crop)
            self._save_unknown(value_mask, f"slot{slot}_value", color_crop=value_crop)
        return entry_id, text

    # ---------- 공개 API ----------

    def read(self, frame_bgr, hint=None) -> GemReading:
        """프레임(BGR ndarray)에서 가공 창 판독. 창이 없으면 None.

        hint: 직전 프레임의 앵커 위치 (자동 감지 시 탐색 비용 절감).
        """
        raw_frame = frame_bgr
        anchor, conf = self.find_anchor(frame_bgr, hint)
        if anchor is None:
            return None
        reading = GemReading(anchor=anchor, confidence=conf, scale=self.scale)
        # 글자·색 판독은 명도 정규화본으로 (HDR·모니터 밝기 흡수). 단 버튼 활성 판정은
        # '밝은 픽셀 비율'이라 정규화하면 비활성이 활성으로 뒤집힌다 → 원본(raw_frame)으로.
        # 밝기 판단은 전체 프레임이 아니라 **젬 창 영역**으로 한다 (21:9·32:9는 좌우가
        # 검은 배경이라 전체 p99가 낮게 나와 정상 화면인데도 정규화가 발동했었다).
        frame_bgr = glyphs.normalize_brightness(
            frame_bgr, sample_rect=self._gem_window_rect(anchor, frame_bgr.shape))

        # 효율·포인트는 밴드 가운데 홀로 놓인 숫자, 효과는 "Lv. N"의 오른쪽 끝 숫자
        # (밴드 오른쪽 모서리에 다이아 광택 대각선이 끼면 그게 마지막 글리프로 잡히므로
        #  숫자 후보 중 최적 매칭을 고르는 "digit_last"를 쓴다 — 고배율에서 실측한 문제)
        reading.willpower = self._read_level(
            frame_bgr, anchor, "level_willpower", "center", reading)
        reading.points = self._read_level(
            frame_bgr, anchor, "level_points", "center", reading)
        reading.effect1 = self._read_level(
            frame_bgr, anchor, "level_effect1", "digit_last", reading)
        reading.effect2 = self._read_level(
            frame_bgr, anchor, "level_effect2", "digit_last", reading)

        # 젬 포인트는 4~20이라 두 자리일 수 있다
        gem_point_crop = self._crop(frame_bgr, anchor, "gem_point")
        gem_point_mask = glyphs.mask_button_text(gem_point_crop)
        reading.gem_point = glyphs.extract_number(
            gem_point_mask, self._digits, scale=self.scale)
        if reading.gem_point is None:
            reading.warnings.append("gem_point 숫자 판독 실패")
            self._save_unknown(gem_point_mask, "gem_point", color_crop=gem_point_crop)
        # 남은 가공 0~9, 최대 가공 5/7/9(등급), 남은 보기 0~9 (allowedDigits)
        reading.attempts = self._read_digit(
            frame_bgr, anchor, "attempts_current", glyphs.mask_button_text, False,
            reading, allowed=set("0123456789"))
        reading.attempts_max = self._read_digit(
            frame_bgr, anchor, "attempts_max", glyphs.mask_button_text, False,
            reading, allowed=set("579"))
        reading.rerolls = self._read_digit(
            frame_bgr, anchor, "reroll_count", glyphs.mask_button_text, False,
            reading, allowed=set("0123456789"))

        for slot in range(4):
            entry_id, text = self._read_slot(frame_bgr, anchor, slot, reading)
            reading.shown_ids.append(entry_id)
            reading.shown_labels.append(text)

        ax, ay = anchor
        for name, (x1, y1, x2, y2) in self.button_boxes.items():
            reading.buttons[name] = (ax + x1, ay + y1, ax + x2, ay + y2)
            # 버튼 활성은 밝은 픽셀 비율 → 반드시 원본으로 (정규화하면 다 활성이 된다)
            reading.enabled[name] = glyphs.is_enabled(
                raw_frame[ay + y1:ay + y2, ax + x1:ax + x2])
        reading.reset_available = reading.enabled.get("reset", False)
        # 남은 초기화 횟수 "초기화 (N/M)". 없어도 치명적이지 않으므로(불리언 폴백)
        # 경고를 남기지 않는다 — complete() 판정을 오염시키지 않기 위함.
        reading.resets = self._read_quiet_digit(
            frame_bgr, anchor, "reset_current", allowed=set("012"))
        reading.resets_max = self._read_quiet_digit(
            frame_bgr, anchor, "reset_max", allowed=set("12"))

        if not reading.stats_consistent():
            reading.warnings.append(
                f"교차검증 불일치: 젬 포인트 {reading.gem_point} != 스탯 합")
        return reading
