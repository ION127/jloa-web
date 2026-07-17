"""상급재련 창 지오메트리 — 앵커("선택 재료" 라벨) 기준 상대 오프셋.

**모든 좌표는 1920×1080 / UI 스케일 100% 기준.** (젬 geometry와 동일 원칙 —
다른 해상도는 앵커 배율 탐색 후 scaled()로 환산)

판독 규칙 (2026-07-09 샘플 13장 실측):
- 단계 텍스트 "N단계 ≫ M단계"는 중앙 정렬이지만 현재 단계 숫자는 항상
  x<앵커-16 에서 끝난다(우측정렬). 다음 단계 숫자는 next_band에서 같은 폰트.
- 진행바: 채움 끝 x가 bar_zero에서 픽셀당 1/4.9% — **현재 단계 내 진행도(0~90)**.
  밴드 exp = (단계 % 10) × 100 + 진행도.
- 선조의 가호: 엠블럼 고정 소켓 6곳 주황 점등 개수 = stack (시계방향 2씩).
- 나베르의 송곳: 엠블럼 아래 골드 텍스트 존재 (gold 픽셀 비율 > 0.05).
- 부위: 첫 필요재료 아이콘 색 (파랑 수호석=방어구 / 빨강 파괴석=무기).
"""


def scaled(regions: dict, scale: float) -> dict:
    if scale == 1.0:
        return dict(regions)
    return {name: tuple(round(v * scale) for v in box)
            for name, box in regions.items()}


def scaled_points(points, scale: float):
    if scale == 1.0:
        return list(points)
    return [tuple(round(v * scale) for v in p) for p in points]


# 앵커 템플릿 원본 ("선택 재료" 흰 라벨, vision_samples/advanced-refining/320.png)
ANCHOR_SOURCE = {"sample": "320.png", "x1": 966, "y1": 503, "x2": 1052, "y2": 531}

# (x1, y1, x2, y2) — 앵커 좌상단(966, 503) 기준 상대 좌표
REGIONS = {
    "level_band": (-61, -148, -16, -114),    # 현재 단계 숫자 (우측정렬, 1~2자리)
    "next_band": (69, -148, 98, -114),       # 다음 단계 숫자 (좌측정렬 — 템플릿 수확용)
    "bar_band": (-204, -101, 288, -87),      # 진행바 전체 (abs 762..1254, y 402..416)
    "naber_band": (234, -251, 374, -225),    # "나베르의 송곳" 골드 텍스트
    "checkbox_breath": (4, 41, 20, 57),      # 숨결 체크박스
    "checkbox_book": (99, 41, 115, 57),      # 책 체크박스
    "material_icon": (-274, 62, -221, 117),  # 첫 필요재료 아이콘 (파랑/빨강)
    "growth_band": (-31, -400, 119, -376),   # "성장 지원 효과 적용" (녹색)
}

# 선조의 가호 소켓 중심 (시계방향: 상→우상→우하→하→좌상→좌하 — 점등 순서)
SOCKETS = [(298, -360), (334, -339), (334, -284), (298, -267),
           (262, -339), (262, -284)]

# 진행바 채움 환산: 진행% = (채움 끝 x_rel - BAR_ZERO_X) / BAR_PX_PER_PCT
BAR_ZERO_X = -154        # abs 812
BAR_PX_PER_PCT = 4.9

# 오버레이 하이라이트/배치용
BUTTONS = {
    "refine": (-83, 234, 168, 270),          # "상급 재련" 버튼
    "slot_breath": (-2, 37, 90, 152),        # 숨결 선택 슬롯
    "slot_book": (93, 37, 185, 152),         # 책 선택 슬롯
}
WINDOW_BOX = (-326, -443, 409, 287)          # 창 전체 (패널 배치 회피용)
