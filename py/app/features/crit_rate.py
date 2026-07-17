"""치명타 적중률 계산 — armory 데이터만으로 캐릭터의 치명타 적중률을 계산.

calloa(www.calloa.net/calculator/crit-rate) breakdown을 armory 직접 파싱으로 재현한다.
소스 매핑(라이브 검증):
  치명 스탯   ArmoryProfile.Stats 치명 툴팁 ("치명타 적중률이 X% 증가" — 게임이 이미 변환)
  장신구      ArmoryEquipment 반지/팔찌/목걸이/귀걸이의 "치명타 적중률 +X%"
  각인        ArmoryEngraving.ArkPassiveEffects[].Description
  진화        ArkPassive.Effects (Name=="진화"), '달인'은 최대중첩 반영
  아크그리드   ArkGrid.Slots[].Tooltip 의 [NP] 임계값(N<=Slot.Point) 합
  깨달음      ArkPassive.Effects (Name=="깨달음"/"도약")의 실제 투자 노드 치명
             + armory 툴팁에 없는 각성 고유 치명(포식자/처단자/분노/사신화)은 하드코딩
백어택 +10%는 토글. 치명타 팻은 '치명 스탯 +160'이라, 캐릭터의 스탯→적중률
변환비(적중률/치명)를 그대로 적용해 가산한다. (엘릭서 치명은 삭제됨 — 콘텐츠 종료)
"""

import json
import os
import re

CRIT = "치명타 적중률"
ACCESSORY_TYPES = ("반지", "팔찌", "목걸이", "귀걸이")
BACK_ATTACK_RATE = 10.0   # 백어택 시 +10% (calloa fromBackAttack)
CRIT_PET_STAT = 160.0     # 치명타 팻: 치명 스탯 +160 (적중률 변환은 캐릭 변환비로 계산)

# armory arkpassive 툴팁엔 치명이 안 잡히지만 실제 적용되는 각성 고유 치명 (calloa 확정).
# key: "직업|각성"(crit_job_table.json 키와 동일) → (치명타 적중률%, 표시이름)
IDENTITY_CRIT = {
    "슬레이어|포식자": (30.0, "포식자"),
    "슬레이어|처단자": (30.0, "처단자"),
    "버서커|광전사의 비기": (50.67, "분노"),
    "소울이터|만월의 집행자": (20.0, "사신화"),
}

_TABLE_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                           "crit_job_table.json")


def _strip(s) -> str:
    return re.sub("<[^>]+>", "", str(s or "")).strip()


def _tooltip_text(node: dict) -> str:
    """ArkPassive/ArkGrid 노드의 ToolTip(escaped JSON) → 모든 Element value 평문."""
    tt = node.get("ToolTip") or node.get("Tooltip") or ""
    try:
        j = json.loads(tt)
    except Exception:
        return _strip(tt)
    parts = []

    def collect(v):
        if isinstance(v, dict):
            if "value" in v:
                collect(v["value"])
            else:
                for x in v.values():
                    collect(x)
        elif isinstance(v, list):
            for x in v:
                collect(x)
        else:
            parts.append(_strip(v))

    collect(j)
    return " ".join(p for p in parts if p)


def _crit_values(text: str):
    """텍스트에서 '치명타 적중률 … N%' 수치 목록. '치명타 피해'는 리터럴 매칭으로 제외.

    라벨과 수치 사이에 '이 추가로', '+' 등 짧은 어구가 끼는 경우(각인 툴팁 등)까지
    포착하되, 다른 숫자/％를 건너뛰지 않도록 [^%\\d] 한정·비탐욕으로 최근접 수치만 취한다.
    """
    return [float(m.group(1)) for m in re.finditer(
        CRIT + r"[^%\d]{0,10}?([0-9]+\.?[0-9]*)\s*%", text)]


# ---------- 소스별 파서 ----------

def stat_info(armory: dict):
    """치명 스탯 → (적중률%, 치명 스탯값). 게임이 스탯 툴팁에 변환값을 직접 써 줌."""
    prof = armory.get("ArmoryProfile") or {}
    for s in prof.get("Stats") or []:
        if s.get("Type") in ("치명", "치명타"):
            try:
                value = float(str(s.get("Value") or "0").replace(",", ""))
            except ValueError:
                value = 0.0
            for line in s.get("Tooltip") or []:
                vals = _crit_values(_strip(line))
                if vals:
                    return vals[0], value
            return 0.0, value
    return 0.0, 0.0


def stat_crit(armory: dict) -> float:
    return stat_info(armory)[0]


def pet_crit(armory: dict) -> float:
    """치명타 팻(치명 +160)을 캐릭터의 적중률/치명 변환비로 환산한 가산치."""
    rate, value = stat_info(armory)
    if value <= 0:
        return 0.0
    return round(CRIT_PET_STAT * rate / value, 2)


def accessory_crit(armory: dict) -> float:
    """반지/팔찌/목걸이/귀걸이 연마의 '치명타 적중률 +X%' 합."""
    total = 0.0
    for e in armory.get("ArmoryEquipment") or []:
        if e.get("Type") not in ACCESSORY_TYPES:
            continue
        total += sum(_crit_values(_tooltip_text(e)))
    return round(total, 2)


def engraving_crit(armory: dict) -> float:
    """아크패시브 각인 설명의 치명타 적중률(예: 아드레날린 최대 중첩 +20%)."""
    eng = armory.get("ArmoryEngraving") or {}
    total = 0.0
    for e in eng.get("ArkPassiveEffects") or []:
        text = _strip(e.get("Description") or e.get("Name"))
        total += sum(_crit_values(text))
    return round(total, 2)


def evolution_crit(armory: dict) -> float:
    """ArkPassive 진화 카테고리 노드 치명 합. '달인'류는 '최대 N중첩' 반영."""
    ap = armory.get("ArkPassive") or {}
    total = 0.0
    for e in ap.get("Effects") or []:
        if e.get("Name") != "진화":
            continue
        text = _tooltip_text(e)
        vals = _crit_values(text)
        if not vals:
            continue
        stack = re.search(r"최대\s*([0-9]+)\s*중첩", text)
        if stack and ("중첩" in text):
            # 스택형(달인): 치명 수치 × 최대중첩
            total += vals[0] * int(stack.group(1))
        else:
            total += sum(vals)
    return round(total, 2)


def arkgrid_crit(armory: dict) -> float:
    """아크그리드 코어의 [NP] 임계 효과 중 슬롯 포인트 이상 도달분 합."""
    ag = armory.get("ArkGrid") or {}
    total = 0.0
    for slot in ag.get("Slots") or []:
        point = slot.get("Point") or 0
        text = _tooltip_text(slot)
        for m in re.finditer(
                r"\[([0-9]+)P\][^\[]*?" + CRIT + r"[이 ]{0,2}([0-9]+\.?[0-9]*)\s*%",
                text):
            if int(m.group(1)) <= point:
                total += float(m.group(2))
    return round(total, 2)


# ---------- 직업/각성 감지 & 깨달음 치명 ----------

def _load_table() -> dict:
    try:
        with open(_TABLE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _awakening_nodes(armory: dict):
    """깨달음/도약 카테고리 노드 목록 [(name, full_text)]."""
    ap = armory.get("ArkPassive") or {}
    nodes = []
    for e in ap.get("Effects") or []:
        if e.get("Name") in ("깨달음", "도약"):
            desc = re.split(r"티어", _strip(e.get("Description")))[-1]
            name = re.sub(r"Lv\.?\s*[0-9]+", "", desc).strip()
            nodes.append((name, _tooltip_text(e)))
    return nodes


def detect_awakening(armory: dict):
    """각성 이름이 깨달음 노드로 등장하는 점을 이용해 (table_key, row) 추정.

    각성 이름(예: 포식자·광전사의 비기·오의난무)은 깨달음 트리의 노드명으로 그대로
    나타난다. 직업(CharacterClassName)으로 교차 검증. 실패 시 크릿노드 겹침으로 폴백.
    """
    table = _load_table()
    node_names = {n for n, _ in _awakening_nodes(armory)}
    cls = (armory.get("ArmoryProfile") or {}).get("CharacterClassName")
    # 1차: 각성 이름이 노드로 존재 + 직업 일치
    for key, row in table.items():
        if row.get("awakening") in node_names and row.get("job") == cls:
            return key, row
    # 2차: 각성 이름만 일치 (직업명 표기 차이 대비)
    for key, row in table.items():
        if row.get("awakening") in node_names:
            return key, row
    # 3차: 크릿노드 겹침 폴백
    best, best_hits = (None, None), 0
    for key, row in table.items():
        conds = {c["condition"] for c in row.get("conditional_crit", [])}
        hits = len(conds & node_names)
        if hits > best_hits:
            best, best_hits = (key, row), hits
    return best


def awakening_crit(armory: dict, table_key: str = None):
    """깨달음/도약 노드의 실제 투자 치명 합 + 각성 고유 치명(하드코딩).

    반환: (총합%, [세부내역]).  세부내역: [{"name","rate","source"}].
    """
    detail = []
    for name, text in _awakening_nodes(armory):
        vals = _crit_values(text)
        if not vals:
            continue
        stack = re.search(r"최대\s*([0-9]+)\s*중첩", text)
        rate = vals[0] * int(stack.group(1)) if (stack and "중첩" in text) else sum(vals)
        detail.append({"name": name, "rate": round(rate, 2), "source": "node"})
    # armory에 없는 각성 고유 치명
    ident = IDENTITY_CRIT.get(table_key)
    if ident is not None:
        rate, disp = ident
        detail.append({"name": disp, "rate": rate, "source": "identity"})
    total = round(sum(d["rate"] for d in detail), 2)
    return total, detail


# ---------- 종합 ----------

def calculate(armory: dict, back_attack: bool = False,
              crit_pet: bool = False) -> dict:
    """치명타 적중률 종합 계산.

    반환 dict:
      breakdown: {stat, accessory, engraving, evolution, arkgrid, awakening}
      subtotal: breakdown 합 (장비+각성)
      back_attack / crit_pet: 적용된 토글 가산치
      total: subtotal + 토글
      awakening_key: 감지된 "직업|각성"
      awakening_detail: 깨달음 치명 세부내역
      conditional: 스킬형 조건부 치명(참고용, 합산 제외)
    """
    table_key, row = detect_awakening(armory)
    awk_total, awk_detail = awakening_crit(armory, table_key)
    breakdown = {
        "stat": stat_crit(armory),
        "accessory": accessory_crit(armory),
        "engraving": engraving_crit(armory),
        "evolution": evolution_crit(armory),
        "arkgrid": arkgrid_crit(armory),
        "awakening": awk_total,
    }
    subtotal = round(sum(breakdown.values()), 2)
    add = 0.0
    if back_attack:
        add += BACK_ATTACK_RATE
    pet = pet_crit(armory) if crit_pet else 0.0
    add += pet
    # 참고용: 합산에 안 넣는 스킬형 조건부만
    cond = [c for c in (row or {}).get("conditional_crit", [])
            if c.get("source") == "skill"] if row else None
    return {
        "breakdown": breakdown,
        "subtotal": subtotal,
        "back_attack": BACK_ATTACK_RATE if back_attack else 0.0,
        "crit_pet": pet,
        "total": round(subtotal + add, 2),
        "awakening_key": table_key,
        "awakening_detail": awk_detail,
        "conditional": cond or None,
    }
