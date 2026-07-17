"""고정폰트 글리프 매칭 유틸 (Phase 6.0) — 캘리브레이션 도구와 런타임이 공유.

핵심 설계 (14개 샘플 실측으로 확정):
- 게임 UI 텍스트는 두 종류로 나뉜다.
  * 버튼/라벨 텍스트: 어두운 배경 위 회색 → 정규화 후 Otsu 이진화
  * 다이아몬드 위 숫자: 색이 있는 텍스처 배경 위 흰색/노란색 → HSV 색 마스크
    (Otsu는 배경의 대각선 무늬를 글자로 오인함 — 실측 확인)
- 숫자는 종횡비를 보존한 채 높이 28로 정규화 후 20×28 캔버스 중앙 배치해 NCC.
  절대 점수는 폰트 크기에 따라 낮아질 수 있지만 argmax 판별은 안정적이다
  (버튼 폰트 템플릿으로 다이아 숫자를 맞춰도 정답이 1위 — 실측 확인).
"""

# 글리프 정규화 상자 — 여기로 리사이즈하므로 **템플릿 매칭 자체는 해상도와 무관**하다.
BOX_W, BOX_H = 20, 28

# 아래 크기 기준은 1920×1080(UI 100%) 실측값. 다른 해상도에선 배율을 곱해 쓴다.
_MIN_GLYPH_H, _MAX_GLYPH_H = 8, 16
_MIN_GLYPH_W, _MAX_GLYPH_W = 2, 14
_MIN_GLYPH_AREA = 10
_MERGE_GAP = 3          # 세로로 끊긴 조각을 잇는 최대 간격
_PLUS_MAX_H = 8         # '+' 글리프의 최대 높이


def _limits(scale: float):
    """1080p 기준 크기 필터를 현재 배율로 환산 (면적은 배율의 제곱)."""
    return {
        "min_h": max(3, round(_MIN_GLYPH_H * scale)),
        "max_h": round(_MAX_GLYPH_H * scale),
        "min_w": max(1, round(_MIN_GLYPH_W * scale)),
        "max_w": round(_MAX_GLYPH_W * scale),
        "min_area": max(4, round(_MIN_GLYPH_AREA * scale * scale)),
        "merge_gap": max(1, round(_MERGE_GAP * scale)),
        "plus_max_h": round(_PLUS_MAX_H * scale),
    }


def _cv2():
    import cv2
    return cv2


def normalize_brightness(bgr, target_p99=235, sample_rect=None):
    """프레임 명도를 표준으로 맞춘다 — HDR·모니터 밝기 차이 흡수.

    글자·아이콘 마스크가 절대 명도 임계(v>150 등)를 쓰므로 어두운 화면에선 글자를
    통째로 놓친다 (실측: 밝기 0.7배에서 판독 실패). gempago가 히스토그램 매칭으로
    푸는 것을 우리는 더 싸게 — 밝은 쪽 상위 1%(p99) 명도를 표준값으로 선형 스케일한다.
    (감마 보정도 시도했으나 게임 배경이 원래 어두워 중간톤 기준이 항상 발동, 다이아
     색을 뒤틀어 역효과였다 — 실측. 선형 스케일만으로 0.5~0.8배 밝기를 전부 흡수한다.)

    sample_rect: 밝기를 잴 영역 (left, top, right, bottom). 21:9·32:9는 좌우가 검은
    배경이라 전체 p99가 낮게 나오므로 젬 창 영역만으로 판단한다. 스케일 자체는 전체에 적용.
    """
    import numpy as np
    cv2 = _cv2()
    if sample_rect is not None:
        x1, y1, x2, y2 = sample_rect
        region = bgr[y1:y2, x1:x2]
    else:
        region = bgr
    value = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)[:, :, 2]
    p99 = float(np.percentile(value, 99))
    # 아주 밝을(원본 정상) 때만 건드리지 않는다 — 정규화가 슬롯 값 색을 미세하게 밀어
    # 색 기반 판독을 흔들었다 (실측: 원본 p99 196에서도 발동해 +4가 깨짐).
    # 반대로 애매하게 어두운(p99 150~200) 화면은 확실히 235로 끌어올려야 경계에서
    # 다이아 숫자가 흔들리지 않는다 (실측: 0.8배 p99 180 근처에서 효율 4 판독 실패).
    if p99 >= 215 or p99 < 30:
        return bgr
    return cv2.convertScaleAbs(bgr, alpha=target_p99 / p99, beta=0)


def mask_bright_text(bgr):
    """다이아몬드 위 숫자 마스크 (텍스처 배경 무시).

    다이아 숫자는 **금색**(H 18~35)이고 글자 테두리만 흰색이다.
    금색 명도 임계가 190이면 획이 가는 부분이 끊겨 '5'가 위아래 두 조각으로
    쪼개지고, 각 조각이 높이 필터(≥8)에 걸려 통째로 사라진다 (2026-07-09 실사용 버그).
    175까지 낮추면 획이 이어지면서도 배경 무늬·광택은 여전히 걸러진다.
    색 범위를 넓히는 쪽(H 5~50)은 금색 테두리가 글자에 달라붙어 실패했다.
    """
    import numpy as np
    cv2 = _cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    white = (v > 195) & (s < 70)
    gold = (v > 175) & (s > 70) & (h >= 18) & (h <= 35)
    return ((white | gold) * np.uint8(255)).astype(np.uint8)


def mask_button_text(bgr):
    """버튼·라벨의 회색 텍스트 마스크 (밝기 정규화 + Otsu)."""
    cv2 = _cv2()
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]


def mask_slot_text(bgr):
    """제시 슬롯의 이름/값 텍스트 마스크.

    배경이 어두워 밝기만으로 충분하다. 값 텍스트는 초록(H≈38)·흰색·빨강이 섞여
    있어 색상 범위를 좁히면 오히려 놓친다 (실측: 'Lv. N 증가'가 색 마스크에서 누락됨).
    """
    import numpy as np
    cv2 = _cv2()
    value = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[:, :, 2]
    return ((value > 150) * np.uint8(255)).astype(np.uint8)


def glyph_components(mask, scale: float = 1.0):
    """글리프 후보 컴포넌트 [(x, y, w, h)] — x 오름차순. 배경 무늬·광택은 크기로 배제."""
    cv2 = _cv2()
    limit = _limits(scale)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    comps = [
        (stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3])
        for i in range(1, count)
        if (limit["min_h"] <= stats[i, 3] <= limit["max_h"]
            and limit["min_w"] <= stats[i, 2] <= limit["max_w"]
            and stats[i, 4] >= limit["min_area"])
    ]
    comps.sort()
    return comps


def _merge_boxes(a, b):
    x = min(a[0], b[0])
    y = min(a[1], b[1])
    x2 = max(a[0] + a[2], b[0] + b[2])
    y2 = max(a[1] + a[3], b[1] + b[3])
    return (x, y, x2 - x, y2 - y)


def _same_glyph(a, b, max_gap=3, min_overlap=0.5):
    """세로로 끊긴 같은 글자의 조각인가 — x 범위가 겹치고 위아래로 가까우면."""
    left = max(a[0], b[0])
    right = min(a[0] + a[2], b[0] + b[2])
    overlap = right - left
    if overlap <= 0 or overlap < min_overlap * min(a[2], b[2]):
        return False
    gap = max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3])
    return gap <= max_gap


def digit_components(mask, scale: float = 1.0):
    """숫자 글리프 후보 — 세로로 끊긴 조각을 먼저 이어 붙인다.

    다이아몬드 위 금색 숫자는 획이 실처럼 얇아 '5'가 위아래 두 조각으로 끊긴다.
    조각 각각은 높이 필터에 걸려 사라지고, 남은 조각(아래 반원)이 '0'으로 오판독됐다
    (2026-07-09 실사용 버그: 'Lv. 5'·효율 5). 임계를 더 낮추면 광택이 붙으므로,
    조각을 x가 겹치고 세로 간격이 좁을 때만 합친다.
    """
    cv2 = _cv2()
    limit = _limits(scale)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    pieces = [
        (stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3])
        for i in range(1, count)
        if (max(2, round(3 * scale)) <= stats[i, 3] <= limit["max_h"]
            and 1 <= stats[i, 2] <= limit["max_w"]
            and stats[i, 4] >= max(3, round(4 * scale * scale)))
    ]
    merged = True
    while merged:
        merged = False
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                if _same_glyph(pieces[i], pieces[j], max_gap=limit["merge_gap"]):
                    box = _merge_boxes(pieces[i], pieces[j])
                    if box[3] <= limit["max_h"] and box[2] <= limit["max_w"]:
                        pieces[i] = box
                        pieces.pop(j)
                        merged = True
                        break
            if merged:
                break
    comps = [p for p in pieces
             if limit["min_h"] <= p[3] <= limit["max_h"]
             and limit["min_w"] <= p[2] <= limit["max_w"]]
    comps.sort()
    return comps


def normalize_glyph(glyph):
    """종횡비 보존 높이 정규화 → BOX_W×BOX_H 중앙 배치."""
    import numpy as np
    cv2 = _cv2()
    scale = BOX_H / glyph.shape[0]
    width = max(1, min(BOX_W, round(glyph.shape[1] * scale)))
    resized = cv2.resize(glyph, (width, BOX_H), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((BOX_H, BOX_W), np.uint8)
    offset = (BOX_W - width) // 2
    canvas[:, offset:offset + width] = resized
    return canvas


def extract_digit(mask, mode, scale: float = 1.0):
    """마스크에서 숫자 글리프 하나를 정규화해 반환.

    mode=True/"last": "Lv. N"처럼 끝이 숫자 · False/"first": 첫 글리프가 숫자
    mode="center": 밴드 가운데에 홀로 놓인 숫자 (효율·포인트 다이아).
        라벨 꼬리 같은 잡티가 가장자리에 끼어도 가운데 글리프를 고른다.
    """
    comps = digit_components(mask, scale)
    if not comps:
        return None
    if mode == "center":
        middle = mask.shape[1] / 2
        x, y, w, h = min(comps, key=lambda c: abs(c[0] + c[2] / 2 - middle))
    elif mode in (True, "last"):
        x, y, w, h = comps[-1]
    else:
        x, y, w, h = comps[0]
    return normalize_glyph(mask[y:y + h, x:x + w])


def extract_number(mask, templates, max_digits=2, scale: float = 1.0):
    """여러 자리 숫자를 왼쪽부터 읽어 정수로 (예: 젬 포인트 11). 실패하면 None."""
    comps = digit_components(mask, scale)[:max_digits]
    if not comps:
        return None
    text = ""
    for x, y, w, h in comps:
        label, _score = match_digit(normalize_glyph(mask[y:y + h, x:x + w]), templates)
        if label is None:
            return None
        text += label
    return int(text)


def match_digit(normalized, templates, threshold=0.30, allowed=None):
    """정규화 글리프 → (숫자 문자, 신뢰도).

    templates: {문자: [정규화 템플릿, ...]} — 같은 숫자라도 버튼/라벨/다이아 폰트가
    크기·굵기가 달라 폰트별 템플릿을 여러 개 두고 최댓값을 취한다.
    allowed: 이 필드에 나올 수 있는 숫자 집합 (예: 효과 레벨은 {'1'..'5'}).
      gempago의 allowedDigits처럼 후보를 좁혀 오판독을 막는다.
    """
    import numpy as np
    cv2 = _cv2()
    if normalized is None:
        return None, 0.0
    source = normalized.astype(np.float32)
    best_label, best_score = None, -1.0
    for label, variants in templates.items():
        if allowed is not None and label not in allowed:
            continue
        for template in variants:
            score = float(cv2.matchTemplate(source, template.astype(np.float32),
                                            cv2.TM_CCOEFF_NORMED)[0][0])
            if score > best_score:
                best_label, best_score = label, score
    if best_score < threshold:
        return None, best_score
    return best_label, best_score


def load_digit_templates(directory):
    """digits/<char>_<n>.png → {문자: [정규화 템플릿, ...]}.

    dot/slash/percent 는 파일명에서 기호로 되돌린다. 템플릿 png 는 이미 정규화 크기
    (BOX_H×BOX_W)로 수확돼 있어 그대로 matchTemplate 대상이 된다.
    """
    import os
    import numpy as np
    cv2 = _cv2()
    reverse = {"dot": ".", "slash": "/", "percent": "%"}
    templates = {}
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".png"):
            continue
        stem = filename.rsplit("_", 1)[0]
        char = reverse.get(stem, stem)
        image = cv2.imdecode(np.fromfile(os.path.join(directory, filename), np.uint8),
                             cv2.IMREAD_GRAYSCALE)
        templates.setdefault(char, []).append(image)
    return templates


def locate_text(mask, template, threshold=0.55):
    """마스크에서 템플릿 문자열의 (신뢰도, x위치). 못 찾으면 (score, None)."""
    import numpy as np
    cv2 = _cv2()
    if template.shape[0] > mask.shape[0] or template.shape[1] > mask.shape[1]:
        return -1.0, None
    result = cv2.matchTemplate(mask.astype(np.float32), template.astype(np.float32),
                               cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    return score, (loc[0] if score >= threshold else None)


def text_color(bgr, mask):
    """값 텍스트의 색 → "green"(증가) | "red"(감소) | "white"(변경·유지·비용·보기).

    문자열 매칭 대신 색으로 증감을 읽는다. "+1 증가"와 "+3 증가"는 한 글자만 달라
    통짜 템플릿 매칭이 전부 1.00으로 나오고, 한글 글리프는 자모가 따로 떨어져
    꼬리말 분리도 불안정했다 (둘 다 실측으로 확인). 색은 게임이 의미로 칠해 준다.
    """
    import numpy as np
    cv2 = _cv2()
    selected = mask > 0
    if not selected.any():
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat = hsv[:, :, 0][selected], hsv[:, :, 1][selected]
    colored = sat > 100
    if ((hue >= 30) & (hue <= 45) & colored).mean() > 0.3:
        return "green"
    if (((hue <= 8) | (hue >= 170)) & colored).mean() > 0.3:
        return "red"
    return "white"


def slot_stat(bgr_name_band, min_pixels=30, scale: float = 1.0):
    """제시 슬롯의 아이콘 다이아 색 → 어떤 스탯인지.

    반환: "willpower"(빨강) | "points"(주황) | "effect1"(초록=좌) | "effect2"(파랑=우)
          | None(무채색 = 다른 항목 보기/가공 비용/가공 상태)

    게임이 스탯마다 색을 고정해 두었고(상 빨강·하 주황·좌 초록·우 파랑),
    제시 슬롯 아이콘도 같은 색을 쓴다. 이름 템플릿 매칭보다 훨씬 견고하다
    (다이아 위 이름은 자간이 넓어 슬롯 이름과 모양이 달라 매칭이 실패했음 — 실측).
    글자(밝은 픽셀)를 피하려 명도 60~160 구간의 채도 높은 픽셀만 본다.
    """
    import numpy as np
    cv2 = _cv2()
    hsv = cv2.cvtColor(bgr_name_band, cv2.COLOR_BGR2HSV)
    hue, sat, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    selected = (sat > 110) & (value > 60) & (value < 160)
    if selected.sum() < min_pixels * scale * scale:   # 면적은 배율의 제곱
        return None
    dominant = int(np.bincount(hue[selected], minlength=180).argmax())
    if dominant <= 10 or dominant >= 170:
        return "willpower"
    if 12 <= dominant <= 28:
        return "points"
    if 45 <= dominant <= 70:
        return "effect1"
    if 95 <= dominant <= 130:
        return "effect2"
    return None


def _is_plus_sign(comp, scale: float = 1.0):
    """'+' 글리프는 정사각형에 가깝고 숫자보다 낮다 ('-'는 너무 얇아 아예 안 잡힌다).

    정사각형 판정은 **비율**로 한다. 절대 허용치(|w-h|<=2)는 저배율에서 무너진다 —
    실측(강제 21:9, scale 0.76): 숫자 '2'가 (w5,h6)이라 |5-6|=1<=2 로 '+'로 오인돼
    통째로 버려지고, 남은 얇은 획이 '1'로 오독됐다. 숫자는 세로로 길다(h/w>=1.2).
    """
    _x, _y, w, h = comp
    return h <= round(_PLUS_MAX_H * scale) and h <= 1.15 * w


def has_plus_sign(mask, scale: float = 1.0):
    comps = glyph_components(mask, scale)
    return bool(comps) and _is_plus_sign(comps[0], scale)


def rightmost_word(mask, scale: float = 1.0, word_len=2):
    """오른쪽 끝 어절(증가/감소 등 word_len 글자)을 tight 크롭해 반환.

    글자 사이 간격보다 큰 공백을 기준으로 마지막 덩어리를 잘라낸다.
    '+'·'-' 기호 판별은 배율에서 무너져서(고배율에선 세로획이 살아 정사각형이 아님),
    비용 증가/감소는 이 어절을 정규화 매칭으로 가른다.
    """
    comps = glyph_components(mask, scale)
    if len(comps) < word_len:
        return tight(mask)
    tail = comps[-word_len:]
    x1 = min(c[0] for c in tail)
    y1 = min(c[1] for c in tail)
    x2 = max(c[0] + c[2] for c in tail)
    y2 = max(c[1] + c[3] for c in tail)
    return mask[y1:y2, x1:x2]


def pick_digit_glyph(mask, mode, scale: float = 1.0):
    """값 텍스트에서 숫자 글리프를 고른다.

    mode="first": "+N 증가" / "N회 증가" — 첫 글리프가 숫자 ('+'는 높이로 배제)
    mode="gap":   "Lv. N 증가"          — 'L','v' 뒤 숫자, 한글 앞의 큰 공백 직전 글리프
    """
    comps = [c for c in glyph_components(mask, scale)
             if not _is_plus_sign(c, scale)]
    if not comps:
        return None
    if mode == "first":
        x, y, w, h = comps[0]
    else:
        gaps = [(comps[i + 1][0] - (comps[i][0] + comps[i][2]), i)
                for i in range(len(comps) - 1)]
        index = max(gaps)[1] if gaps else 0
        x, y, w, h = comps[index]
    return normalize_glyph(mask[y:y + h, x:x + w])


def digit_glyph_candidates(mask, scale: float = 1.0):
    """값 텍스트 안의 숫자 후보 글리프 전부 (정규화본, x 오름차순). 기호('+')는 배제.

    "Lv. N 증가"의 숫자를 '가장 큰 공백 직전 글리프'로 고르던 기하 규칙은 저배율에서
    무너진다 — 한글('증가') 컴포넌트가 크기 필터에 걸려 사라지면 최대 공백이 'v'와 'N'
    사이가 되어 'v'를 집는다 (실측: 강제 21:9, scale 0.76).
    → 후보를 전부 돌려주고, 호출자가 **허용 숫자 집합에서 최고 점수**를 고른다.
    (한글은 숫자 템플릿과 낮게 맞으므로 후보에 섞여 있어도 해가 없다.)
    """
    comps = [c for c in glyph_components(mask, scale)
             if not _is_plus_sign(c, scale)]
    return [normalize_glyph(mask[y:y + h, x:x + w]) for x, y, w, h in comps]


def match_text(mask, templates, threshold=0.55):
    """이진 마스크를 라벨된 텍스트 템플릿들과 비교 → (라벨, 신뢰도).

    관측·템플릿 모두 tight로 자른 뒤 같은 크기로 정규화해 **한 점에서만** 비교한다.
    슬라이딩 매칭을 쓰면 "+1 증가"/"+3 증가"처럼 한 글자만 다른 문자열이 공통
    부분("증가")에 정렬돼 전부 1.00으로 나온다 (실측으로 확인한 오판독 원인).

    templates 값은 템플릿 하나 또는 **변형 목록**(배율별로 수확한 것)이며 최댓값을 취한다.
    """
    import numpy as np
    cv2 = _cv2()
    observed = tight(mask)
    if observed is None:
        return None, 0.0
    best_label, best_score = None, -1.0
    for label, variants in templates.items():
        if not isinstance(variants, (list, tuple)):
            variants = [variants]
        for template in variants:
            resized = cv2.resize(observed, (template.shape[1], template.shape[0]),
                                 interpolation=cv2.INTER_AREA)
            score = float(cv2.matchTemplate(resized.astype(np.float32),
                                            template.astype(np.float32),
                                            cv2.TM_CCOEFF_NORMED)[0][0])
            # 종횡비가 크게 다르면 다른 문자열 (예: "유지" vs "+100% 증가")
            ratio = ((observed.shape[1] / observed.shape[0])
                     / (template.shape[1] / template.shape[0]))
            if not 0.75 <= ratio <= 1.33:
                score -= 0.5
            if score > best_score:
                best_label, best_score = label, score
    if best_score < threshold:
        return None, best_score
    return best_label, best_score


def tight(mask):
    """비어있지 않은 영역으로 잘라낸 마스크 (없으면 None)."""
    cv2 = _cv2()
    coords = cv2.findNonZero(mask)
    if coords is None:
        return None
    x, y, w, h = cv2.boundingRect(coords)
    return mask[y:y + h, x:x + w]


def is_enabled(bgr, bright_ratio=0.035):
    """버튼 활성/비활성 판정 — 게임은 사용 불가 버튼의 글자를 어둡게 깐다.

    실측 밝은 픽셀 비율: 비활성 0.000~0.022, 활성 0.040~0.091 → 경계 0.035.
    주의: '가공 하기'는 골드가 부족해도 비활성이 되므로 행동 가능 여부의
    근거로만 쓰고, 추천 계산은 판독한 남은 횟수를 쓴다.
    """
    cv2 = _cv2()
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return float((gray > 150).mean()) >= bright_ratio
