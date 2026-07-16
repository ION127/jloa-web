const CHARACTER_API_BASE_URL = "https://api.jloa.cloud";
const CHARACTER_RECENT_KEY = "jloa.recentCharacters";
const CHARACTER_MAX_RECENT = 5;

const characterForm = document.querySelector("[data-character-form]");
const characterInput = document.querySelector("[data-character-input]");
const characterLoading = document.querySelector("[data-character-loading]");
const characterMessage = document.querySelector("[data-character-message]");
const characterView = document.querySelector("[data-character-view]");
const refreshButton = document.querySelector("[data-character-refresh]");
const rosterLoading = document.querySelector("[data-roster-loading]");
const rosterView = document.querySelector("[data-roster-view]");
const rosterList = document.querySelector("[data-roster-list]");
const rosterError = document.querySelector("[data-roster-error]");
const rosterRetryButton = document.querySelector("[data-roster-retry]");
const itemDialog = document.querySelector("[data-item-dialog]");
const characterTabButtons = [...document.querySelectorAll("[data-character-tab]")];
const characterTabPanels = [...document.querySelectorAll("[data-character-panel]")];
const characterTabOpeners = [...document.querySelectorAll("[data-open-character-tab]")];
const initialParams = new URLSearchParams(window.location.search);
const requestedSection = window.location.hash
  ? decodeURIComponent(window.location.hash.slice(1))
  : "";
const CHARACTER_TAB_ALIASES = Object.freeze({
  overview: "character",
  equipment: "character",
  build: "character",
  ark: "character",
  combat: "character",
  stats: "character",
  gems: "character",
  engravings: "character",
  "ark-passive": "character",
  "ark-grid": "character",
  cards: "character",
  skills: "character",
  siblings: "roster",
  roster: "roster",
  collection: "extras",
  appearance: "extras",
  "panel-overview": "character",
  "panel-equipment": "character",
  "panel-build": "character",
  "panel-ark": "character",
  "panel-combat": "character",
  "panel-collection": "extras",
  "panel-character": "character",
  "panel-roster": "roster",
  "panel-extras": "extras",
});
const requestedTabValue = initialParams.get("tab") || requestedSection || "character";
const requestedTab = CHARACTER_TAB_ALIASES[requestedTabValue] || requestedTabValue;

const detailRegistry = new Map();
let detailSequence = 0;
let currentCharacterName = "";
let rosterLoadedName = "";
let rosterRequestName = "";
let rosterAbortController = null;
let activeCharacterTab = characterTabPanels.some(
  (panel) => panel.dataset.characterPanel === requestedTab,
)
  ? requestedTab
  : "character";

function valueOr(value, fallback = "-") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

function setText(selector, value, fallback = "-") {
  const element = document.querySelector(selector);
  if (element) element.textContent = String(valueOr(value, fallback));
}

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function element(tagName, className = "", text = "") {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}

function imageElement(src, alt, className = "") {
  const image = document.createElement("img");
  image.src = src || "/jloa-icon.png";
  image.alt = alt || "";
  image.loading = "lazy";
  image.decoding = "async";
  if (className) image.className = className;
  return image;
}

function htmlToPlainText(value) {
  if (value === null || value === undefined) return "";
  const prepared = String(value)
    .replace(/\|\|/g, "\n")
    .replace(/\|/g, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<img\b[^>]*>/gi, " ");
  const parsed = new DOMParser().parseFromString(prepared, "text/html");
  return (parsed.body.textContent || "")
    .replace(/\u00a0/g, " ")
    .replace(/\r/g, "");
}

function cleanInline(value) {
  return htmlToPlainText(value).replace(/\s+/g, " ").trim();
}

function splitCleanLines(value) {
  return htmlToPlainText(value)
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function updateCharacterHistory() {
  if (!currentCharacterName) return;
  const params = new URLSearchParams();
  params.set("name", currentCharacterName);
  if (activeCharacterTab !== "character") params.set("tab", activeCharacterTab);
  history.replaceState(null, "", `/character.html?${params.toString()}`);
}

function activateCharacterTab(
  tabName,
  { updateHistory = true, scroll = false, focus = false } = {},
) {
  const validTab = characterTabPanels.some(
    (panel) => panel.dataset.characterPanel === tabName,
  )
    ? tabName
    : "character";

  activeCharacterTab = validTab;

  characterTabButtons.forEach((button) => {
    const isActive = button.dataset.characterTab === validTab;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
    button.tabIndex = isActive ? 0 : -1;
    if (isActive && focus) button.focus({ preventScroll: true });
  });

  characterTabPanels.forEach((panel) => {
    const isActive = panel.dataset.characterPanel === validTab;
    panel.hidden = !isActive;
    panel.classList.toggle("is-active", isActive);
  });

  if (updateHistory) updateCharacterHistory();
  if (validTab === "roster" && currentCharacterName) {
    loadRosterSummary(currentCharacterName);
  }
  if (scroll) {
    document.getElementById("character-tabs")?.scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }
}

function parseJson(value) {
  if (typeof value !== "string") return value;
  const normalized = value.trim();
  if (!normalized || (!normalized.startsWith("{") && !normalized.startsWith("["))) {
    return null;
  }
  try {
    return JSON.parse(normalized);
  } catch {
    return null;
  }
}

const ignoredTooltipKeys = new Set([
  "type",
  "iconPath",
  "imagePath",
  "iconGrade",
  "slotData",
  "bEquip",
  "temporary",
  "trash",
  "cardIcon",
  "islandIcon",
  "petBorder",
  "blackListIcon",
  "battleItemTypeIcon",
  "advBookIcon",
  "isBookMark",
  "notRegistered",
  "lock",
  "maximum",
  "minimum",
  "valueType",
  "forceValue",
  "itemIrochiCount",
  "town",
  "friendship",
  "rtString",
]);

function collectTooltipText(value, output, key = "") {
  if (value === null || value === undefined || typeof value === "boolean") return;

  if (typeof value === "string") {
    const parsed = parseJson(value);
    if (parsed && typeof parsed === "object") {
      collectTooltipText(parsed, output, key);
      return;
    }
    splitCleanLines(value).forEach((line) => output.push(line));
    return;
  }

  if (Array.isArray(value)) {
    value.forEach((item) => collectTooltipText(item, output, key));
    return;
  }

  if (typeof value === "object") {
    Object.entries(value).forEach(([childKey, childValue]) => {
      if (ignoredTooltipKeys.has(childKey)) return;
      collectTooltipText(childValue, output, childKey);
    });
  }
}

function tooltipLines(tooltip, additionalLines = [], maxLines = 44) {
  const collected = [];
  collectTooltipText(additionalLines, collected);
  collectTooltipText(tooltip, collected);

  const unique = [];
  const seen = new Set();
  collected.forEach((line) => {
    const normalized = line.replace(/\s+/g, " ").trim();
    if (
      !normalized ||
      /^https?:\/\//i.test(normalized) ||
      /efui_iconatlas|emoticon_/i.test(normalized) ||
      /^[-+]?\d[\d,.]*$/.test(normalized)
    ) {
      return;
    }
    const key = normalized.toLocaleLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    unique.push(normalized.length > 700 ? `${normalized.slice(0, 697)}…` : normalized);
  });
  return unique.slice(0, maxLines);
}

function findDeepValue(value, targetKey) {
  if (!value || typeof value !== "object") return undefined;
  if (Object.prototype.hasOwnProperty.call(value, targetKey)) return value[targetKey];
  for (const child of Object.values(value)) {
    const found = findDeepValue(child, targetKey);
    if (found !== undefined) return found;
  }
  return undefined;
}

function gradeClass(grade) {
  const normalized = cleanInline(grade);
  const classes = {
    고대: "grade-ancient",
    유물: "grade-relic",
    전설: "grade-legendary",
    영웅: "grade-epic",
    희귀: "grade-rare",
    고급: "grade-uncommon",
    일반: "grade-normal",
  };
  return classes[normalized] || "";
}

function setSectionCount(key, count, unit = "개") {
  const target = document.querySelector(`[data-section-count="${key}"]`);
  if (target) target.textContent = `${count}${unit}`;
}

function renderEmpty(container, message) {
  container.replaceChildren(element("p", "section-empty", message));
}

function formatFetchedAt(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function normalizeCharacterName(value) {
  return String(value || "").trim();
}

function getRecentCharacters() {
  try {
    const stored = JSON.parse(localStorage.getItem(CHARACTER_RECENT_KEY) || "[]");
    return Array.isArray(stored)
      ? stored.filter((name) => typeof name === "string").slice(0, CHARACTER_MAX_RECENT)
      : [];
  } catch {
    return [];
  }
}

function saveRecentCharacter(name) {
  const normalized = normalizeCharacterName(name);
  const next = [
    normalized,
    ...getRecentCharacters().filter(
      (item) => item.toLocaleLowerCase() !== normalized.toLocaleLowerCase(),
    ),
  ].slice(0, CHARACTER_MAX_RECENT);
  try {
    localStorage.setItem(CHARACTER_RECENT_KEY, JSON.stringify(next));
  } catch {
    // 저장소가 차단되어도 조회 기능은 계속 사용한다.
  }
}

function registerDetail({ title, subtitle, icon, tooltip, lines = [] }) {
  detailSequence += 1;
  const id = `detail-${detailSequence}`;
  detailRegistry.set(id, {
    title: cleanInline(title) || "상세 정보",
    subtitle: cleanInline(subtitle) || "Open API 상세 정보",
    icon: icon || "/jloa-icon.png",
    lines: tooltipLines(tooltip, lines),
  });
  return id;
}

function createDetailButton(detailId, label = "상세 보기") {
  const button = element("button", "detail-trigger");
  button.type = "button";
  button.dataset.detailId = detailId;
  button.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 12h10M12 7v10"/></svg>';
  button.append(document.createTextNode(label));
  return button;
}

function openDetail(detailId) {
  const detail = detailRegistry.get(detailId);
  if (!detail || !itemDialog) return;

  const icon = itemDialog.querySelector("[data-dialog-icon]");
  const title = itemDialog.querySelector("[data-dialog-title]");
  const subtitle = itemDialog.querySelector("[data-dialog-subtitle]");
  const body = itemDialog.querySelector("[data-dialog-body]");

  if (icon) {
    icon.src = detail.icon;
    icon.alt = "";
  }
  if (title) title.textContent = detail.title;
  if (subtitle) subtitle.textContent = detail.subtitle;

  if (body) {
    body.replaceChildren();
    if (detail.lines.length === 0) {
      body.append(element("p", "dialog-empty", "표시할 추가 상세 정보가 없습니다."));
    } else {
      const list = element("ul", "dialog-detail-list");
      detail.lines.forEach((line) => list.append(element("li", "", line)));
      body.append(list);
    }
  }

  if (typeof itemDialog.showModal === "function") {
    itemDialog.showModal();
  } else {
    itemDialog.setAttribute("open", "");
  }
}

function showMessage(title, description) {
  if (characterLoading) characterLoading.hidden = true;
  if (characterView) characterView.hidden = true;
  if (!characterMessage) return;
  characterMessage.hidden = false;
  setText("[data-message-title]", title, "캐릭터 정보를 불러오지 못했습니다.");
  setText("[data-message-description]", description, "잠시 후 다시 시도해 주세요.");
}

function showLoading() {
  if (characterMessage) characterMessage.hidden = true;
  if (characterView) characterView.hidden = true;
  if (characterLoading) characterLoading.hidden = false;
}

function showView() {
  if (characterLoading) characterLoading.hidden = true;
  if (characterMessage) characterMessage.hidden = true;
  if (characterView) characterView.hidden = false;
}

function renderProfile(profile, arkPassive, response) {
  setText("[data-profile-server]", profile.ServerName, "서버 정보 없음");
  setText("[data-roster-server]", profile.ServerName, "서버 정보 없음");
  setText("[data-profile-class]", profile.CharacterClassName, "클래스 정보 없음");
  setText("[data-profile-name]", profile.CharacterName, currentCharacterName);
  setText("[data-profile-title]", profile.Title, "칭호 없음");
  setText("[data-profile-guild]", profile.GuildName, "길드 미가입");
  setText("[data-profile-item-level]", profile.ItemAvgLevel);
  setText("[data-profile-combat-power]", profile.CombatPower);
  setText("[data-profile-level]", profile.CharacterLevel);
  setText("[data-profile-expedition]", profile.ExpeditionLevel);
  setText("[data-profile-honor]", profile.HonorPoint);
  setText(
    "[data-profile-skill-point]",
    profile.UsingSkillPoint !== null && profile.UsingSkillPoint !== undefined
      ? `${profile.UsingSkillPoint} / ${valueOr(profile.TotalSkillPoint, "-")}`
      : "-",
  );

  const arkTitle = document.querySelector("[data-profile-ark-title]");
  if (arkTitle) {
    const title = cleanInline(arkPassive?.Title);
    arkTitle.hidden = !title;
    arkTitle.textContent = title ? `아크 패시브 · ${title}` : "";
  }

  const image = document.querySelector("[data-profile-image]");
  if (image) {
    if (profile.CharacterImage) {
      image.src = profile.CharacterImage;
      image.alt = `${profile.CharacterName || currentCharacterName} 캐릭터 이미지`;
      image.classList.remove("is-placeholder");
    } else {
      image.src = "/jloa-icon.png";
      image.alt = "";
      image.classList.add("is-placeholder");
    }
  }

  const source = document.querySelector("[data-profile-source]");
  const fetchedAt = formatFetchedAt(response.fetched_at);
  let sourceLabel = "OPEN API 최신 정보";
  let sourceMessage = "로스트아크 Open API에서 새로 조회한 정보입니다.";
  let sourceClass = "";

  if (response.stale) {
    sourceLabel = "저장 데이터 · 갱신 지연";
    sourceMessage = "Open API 갱신 실패로 JLOA DB에 저장된 정보를 표시합니다.";
    sourceClass = "is-stale";
  } else if (response.cached) {
    sourceLabel = "JLOA 스마트 캐시";
    sourceMessage = "JLOA DB의 최근 3분 이내 정보를 표시합니다.";
    sourceClass = "is-cache";
  }

  if (source) {
    source.textContent = sourceLabel;
    source.classList.remove("is-cache", "is-stale");
    if (sourceClass) source.classList.add(sourceClass);
  }
  setText(
    "[data-profile-cache-meta]",
    `${sourceMessage}${fetchedAt ? ` · 조회 시각 ${fetchedAt}` : ""}`,
    sourceMessage,
  );
}

function renderStats(profile, arkPassive) {
  const statsContainer = document.querySelector("[data-combat-stats]");
  const factsContainer = document.querySelector("[data-profile-facts]");
  const tendencyContainer = document.querySelector("[data-tendencies]");
  const stats = toArray(profile.Stats);
  const tendencies = toArray(profile.Tendencies);

  if (statsContainer) {
    statsContainer.replaceChildren();
    if (stats.length === 0) {
      renderEmpty(statsContainer, "전투 특성 정보가 없습니다.");
    } else {
      stats.forEach((stat) => {
        const card = element("article", "stat-card");
        card.append(element("span", "", valueOr(stat.Type, "특성")));
        card.append(element("strong", "", valueOr(stat.Value)));
        const description = tooltipLines(stat.Tooltip, [], 2).join(" · ");
        if (description) card.title = description;
        statsContainer.append(card);
      });
    }
  }
  setSectionCount("stats", stats.length);

  if (factsContainer) {
    factsContainer.replaceChildren();
    const facts = [
      ["서버", profile.ServerName],
      ["클래스", profile.CharacterClassName],
      ["길드", profile.GuildName || "미가입"],
      ["길드 등급", profile.GuildMemberGrade],
      ["칭호", profile.Title],
      [
        "영지",
        profile.TownName
          ? `${profile.TownName}${profile.TownLevel ? ` · Lv.${profile.TownLevel}` : ""}`
          : "",
      ],
      ["아크 패시브", arkPassive?.IsArkPassive ? valueOr(arkPassive.Title, "활성화") : "비활성화"],
    ].filter(([, value]) => value !== null && value !== undefined && value !== "");

    facts.forEach(([label, value]) => {
      const row = element("div");
      row.append(element("dt", "", label), element("dd", "", value));
      factsContainer.append(row);
    });
  }

  if (tendencyContainer) {
    tendencyContainer.replaceChildren();
    if (tendencies.length === 0) {
      renderEmpty(tendencyContainer, "성향 정보가 없습니다.");
    } else {
      tendencies.forEach((tendency) => {
        const card = element("article", "tendency-card");
        card.append(
          element("span", "", valueOr(tendency.Type, "성향")),
          element("strong", "", valueOr(tendency.Point)),
        );
        tendencyContainer.append(card);
      });
    }
  }
  setSectionCount("tendencies", tendencies.length);
}

function equipmentHighlights(item) {
  const itemName = cleanInline(item.Name);
  const noisePattern =
    /거래 불가|거래 제한 아이템 레벨|캐릭터 귀속|분해불가|품질 업그레이드 불가|장착 제한|내구도|제작\]|아이템 레벨|^품질(?:\s|$)|^(기본 효과|추가 효과)$/;
  const interestPattern =
    /아이템 레벨|품질|상급 재련|재련|초월|엘릭서|기본 효과|추가 효과|무기 공격력|공격력|최대 생명력|방어력|깨달음|도약|진화|각인|활성도|치명|특화|신속|피해|재사용/;
  return tooltipLines(item.Tooltip, [], 34)
    .filter((line) => line !== itemName && !noisePattern.test(line))
    .filter((line) => interestPattern.test(line))
    .slice(0, 2);
}

function createEquipmentCard(item) {
  const parsedTooltip = parseJson(item.Tooltip);
  const qualityValue = Number(findDeepValue(parsedTooltip, "qualityValue"));
  const quality = Number.isFinite(qualityValue) && qualityValue >= 0 ? qualityValue : null;
  const grade = cleanInline(item.Grade);
  const card = element("article", `equipment-card ${gradeClass(grade)}`.trim());
  const main = element("div", "equipment-card-main");
  const iconWrap = element("div", "item-icon");
  iconWrap.append(imageElement(item.Icon, ""));
  if (quality !== null) iconWrap.append(element("span", "item-quality", quality));

  const copy = element("div", "item-copy");
  copy.append(
    element("span", "", `${valueOr(item.Type, "장비")} · ${valueOr(grade, "등급 정보 없음")}`),
    element("strong", "", cleanInline(item.Name) || "이름 정보 없음"),
  );
  const highlights = equipmentHighlights(item);
  if (highlights.length > 0) {
    const list = element("ul", "item-highlights");
    highlights.forEach((line) => list.append(element("li", "", line)));
    copy.append(list);
  }
  main.append(iconWrap, copy);
  card.append(main);

  if (quality !== null) {
    const track = element("div", "quality-track");
    const bar = element("i");
    bar.style.width = `${Math.min(Math.max(quality, 0), 100)}%`;
    track.append(bar);
    card.append(track);
  }

  const detailId = registerDetail({
    title: item.Name,
    subtitle: `${valueOr(item.Type, "장비")} · ${valueOr(grade, "등급 정보 없음")}`,
    icon: item.Icon,
    tooltip: item.Tooltip,
    lines: [
      quality !== null ? `품질 ${quality}` : "",
      ...highlights,
    ],
  });
  card.append(createDetailButton(detailId, "장비 세부 옵션"));
  return card;
}

function renderEquipment(equipment) {
  const container = document.querySelector("[data-equipment]");
  const items = toArray(equipment);
  if (!container) return;
  container.replaceChildren();
  setSectionCount("equipment", items.length);

  if (items.length === 0) {
    renderEmpty(container, "장착 장비 정보가 없습니다.");
    return;
  }

  const armorTypes = new Set(["무기", "투구", "상의", "하의", "장갑", "어깨"]);
  const accessoryTypes = new Set(["목걸이", "귀걸이", "반지", "어빌리티 스톤", "팔찌"]);
  const groups = [
    {
      title: "무기 · 방어구",
      description: "강화 · 재련 · 장비 효과",
      items: items.filter((item) => armorTypes.has(cleanInline(item.Type))),
    },
    {
      title: "악세서리 · 어빌리티 스톤",
      description: "특성 · 깨달음 · 세부 옵션",
      items: items.filter((item) => accessoryTypes.has(cleanInline(item.Type))),
    },
    {
      title: "특수 장비",
      description: "팔찌 · 보주 · 특수 슬롯",
      items: items.filter(
        (item) =>
          !armorTypes.has(cleanInline(item.Type)) &&
          !accessoryTypes.has(cleanInline(item.Type)),
      ),
    },
  ].filter((group) => group.items.length > 0);

  groups.forEach((group) => {
    const section = element("section", "equipment-group");
    const heading = element("div", "equipment-group-head");
    heading.append(element("h3", "", group.title), element("span", "", group.description));
    const grid = element("div", "equipment-card-grid");
    group.items.forEach((item) => grid.append(createEquipmentCard(item)));
    section.append(heading, grid);
    container.append(section);
  });
}

function renderGemSummary(container, effects) {
  container.replaceChildren();
  const skills = toArray(effects?.Skills);
  const damageCount = skills.filter((effect) =>
    toArray(effect.Description).some((description) => cleanInline(description).includes("피해")),
  ).length;
  const cooldownCount = skills.filter((effect) =>
    toArray(effect.Description).some((description) =>
      cleanInline(description).includes("재사용 대기시간"),
    ),
  ).length;
  const overall = cleanInline(effects?.Description) || "기본 공격력 효과 정보 없음";
  const card = element("article", "gem-summary-card gem-summary-card--wide");
  card.append(
    element("span", "", `피해 ${damageCount} · 재사용 ${cooldownCount}`),
    element("strong", "", overall),
  );
  container.append(card);
}

function renderGems(gemSection) {
  const summaryContainer = document.querySelector("[data-gem-summary]");
  const container = document.querySelector("[data-gems]");
  if (!summaryContainer || !container) return;

  const gems = toArray(gemSection?.Gems);
  const effects = gemSection?.Effects || {};
  const effectMap = new Map();
  toArray(effects.Skills).forEach((effect) => {
    const slot = Number(effect.GemSlot);
    const stored = effectMap.get(slot) || [];
    stored.push(effect);
    effectMap.set(slot, stored);
  });

  setSectionCount("gems", gems.length);
  renderGemSummary(summaryContainer, effects);
  container.replaceChildren();

  if (gems.length === 0) {
    renderEmpty(container, "장착 보석 정보가 없습니다.");
    return;
  }

  gems.forEach((gem) => {
    const slotEffects = effectMap.get(Number(gem.Slot)) || [];
    const descriptions = slotEffects.flatMap((effect) => toArray(effect.Description));
    const hasDamage = descriptions.some((description) => cleanInline(description).includes("피해"));
    const hasCooldown = descriptions.some((description) =>
      cleanInline(description).includes("재사용 대기시간"),
    );
    const typeClass = hasDamage ? "is-damage" : hasCooldown ? "is-cooldown" : "";
    const typeLabel = hasDamage ? "피해 증가" : hasCooldown ? "재사용 감소" : "보석 효과";
    const card = element("article", `gem-card ${gradeClass(gem.Grade)} ${typeClass}`.trim());
    const body = element("div", "gem-card-body");
    const icon = element("div", "gem-icon");
    icon.append(
      imageElement(gem.Icon, ""),
      element("span", "gem-level", valueOr(gem.Level, "-")),
    );
    const copy = element("div", "gem-copy");
    copy.append(
      element("span", "", `${typeLabel} · ${valueOr(gem.Grade, "등급 정보 없음")}`),
      element("strong", "", cleanInline(gem.Name) || `${valueOr(gem.Level)}레벨 보석`),
    );
    body.append(icon, copy);
    card.append(body);

    if (slotEffects.length > 0) {
      card.append(
        element(
          "p",
          "gem-effect-name",
          slotEffects.map((effect) => cleanInline(effect.Name)).filter(Boolean).join(" · "),
        ),
      );
      const list = element("ul", "gem-effect-list");
      slotEffects.forEach((effect) => {
        toArray(effect.Description).forEach((description) => {
          list.append(element("li", "", cleanInline(description)));
        });
        const option = cleanInline(effect.Option);
        if (option) list.append(element("li", "", option));
      });
      card.append(list);
    }

    const directLines = slotEffects.flatMap((effect) => [
      cleanInline(effect.Name),
      ...toArray(effect.Description).map(cleanInline),
      cleanInline(effect.Option),
    ]);
    const detailId = registerDetail({
      title: gem.Name,
      subtitle: `${valueOr(gem.Level, "-")}레벨 · ${typeLabel}`,
      icon: gem.Icon,
      tooltip: gem.Tooltip,
      lines: directLines,
    });
    card.append(createDetailButton(detailId, "보석 상세 효과"));
    container.append(card);
  });
}

function renderEngravings(engravingSection) {
  const container = document.querySelector("[data-engravings]");
  if (!container) return;
  const entries =
    toArray(engravingSection?.ArkPassiveEffects).length > 0
      ? toArray(engravingSection.ArkPassiveEffects)
      : toArray(engravingSection?.Effects).length > 0
        ? toArray(engravingSection.Effects)
        : toArray(engravingSection?.Engravings);

  container.replaceChildren();
  setSectionCount("engravings", entries.length);
  if (entries.length === 0) {
    renderEmpty(container, "활성화된 각인 정보가 없습니다.");
    return;
  }

  entries.forEach((entry, index) => {
    const card = element("article", "engraving-card");
    const markText = cleanInline(entry.Name).slice(0, 1) || String(index + 1);
    const mark = element("span", "engraving-mark", markText);
    const copy = element("div");
    copy.append(element("h3", "", cleanInline(entry.Name) || `각인 ${index + 1}`));
    const meta = element("div", "engraving-meta");
    if (entry.Grade) meta.append(element("span", "", cleanInline(entry.Grade)));
    if (entry.Level !== null && entry.Level !== undefined && Number(entry.Level) > 0) {
      meta.append(element("span", "", `Lv.${entry.Level}`));
    }
    if (
      entry.AbilityStoneLevel !== null &&
      entry.AbilityStoneLevel !== undefined &&
      Number(entry.AbilityStoneLevel) > 0
    ) {
      meta.append(element("span", "", `어빌리티 스톤 Lv.${entry.AbilityStoneLevel}`));
    }
    if (meta.childElementCount > 0) copy.append(meta);
    card.append(mark, copy);

    const description = cleanInline(entry.Description);

    const detailId = registerDetail({
      title: entry.Name,
      subtitle: `각인 효과${entry.Grade ? ` · ${cleanInline(entry.Grade)}` : ""}`,
      icon: entry.Icon,
      tooltip: entry.Tooltip,
      lines: [description],
    });
    if (detailRegistry.get(detailId)?.lines.length > 0) {
      const button = createDetailButton(detailId, "각인 설명");
      button.style.gridColumn = "1 / -1";
      card.append(button);
    }
    container.append(card);
  });
}

function arkPointClass(name) {
  const normalized = cleanInline(name);
  if (normalized === "진화") return "is-evolution";
  if (normalized === "깨달음") return "is-realization";
  if (normalized === "도약") return "is-leap";
  return "";
}

function renderArkPassive(arkPassive) {
  const pointsContainer = document.querySelector("[data-ark-points]");
  const effectsContainer = document.querySelector("[data-ark-effects]");
  if (!pointsContainer || !effectsContainer) return;
  const points = toArray(arkPassive?.Points);
  const effects = toArray(arkPassive?.Effects);
  setSectionCount("ark-passive", effects.length);

  pointsContainer.replaceChildren();
  if (points.length === 0) {
    renderEmpty(pointsContainer, "아크 패시브 포인트 정보가 없습니다.");
  } else {
    points.forEach((point) => {
      const card = element("article", `ark-point-card ${arkPointClass(point.Name)}`.trim());
      card.append(
        element("span", "", valueOr(point.Name, "아크 패시브")),
        element("strong", "", valueOr(point.Value)),
      );
      const description = cleanInline(point.Description);
      if (description) card.title = description;
      pointsContainer.append(card);
    });
  }

  effectsContainer.replaceChildren();
  if (effects.length === 0) {
    renderEmpty(effectsContainer, "활성화된 아크 패시브 효과가 없습니다.");
    return;
  }

  const groups = new Map();
  effects.forEach((effect) => {
    const name = cleanInline(effect.Name) || "기타";
    const stored = groups.get(name) || [];
    stored.push(effect);
    groups.set(name, stored);
  });

  groups.forEach((groupEffects, groupName) => {
    const group = element("section", "ark-effect-group");
    const heading = element("div", "ark-effect-group-head");
    heading.append(
      element("h3", "", groupName),
      element("span", "", `${groupEffects.length}개 효과`),
    );
    const grid = element("div", "ark-effect-grid");
    groupEffects.forEach((effect) => {
      const card = element("article", "ark-effect-card");
      card.append(imageElement(effect.Icon, ""));
      const copy = element("div");
      const description = cleanInline(effect.Description);
      copy.append(
        element("strong", "", description || valueOr(effect.Name, "아크 패시브 효과")),
        element("span", "", groupName),
      );
      card.append(copy);
      const detailId = registerDetail({
        title: description || effect.Name,
        subtitle: `${groupName} 아크 패시브`,
        icon: effect.Icon,
        tooltip: effect.ToolTip || effect.Tooltip,
        lines: [description],
      });
      card.append(createDetailButton(detailId, "효과 설명"));
      grid.append(card);
    });
    group.append(heading, grid);
    effectsContainer.append(group);
  });
}

function renderArkGrid(arkGrid) {
  const effectsContainer = document.querySelector("[data-grid-effects]");
  const slotsContainer = document.querySelector("[data-grid-slots]");
  if (!effectsContainer || !slotsContainer) return;
  const effects = toArray(arkGrid?.Effects);
  const slots = toArray(arkGrid?.Slots);
  setSectionCount("ark-grid", slots.length, "개 코어");

  effectsContainer.replaceChildren();
  if (effects.length === 0) {
    renderEmpty(effectsContainer, "아크 그리드 합산 효과가 없습니다.");
  } else {
    effects.forEach((effect) => {
      const card = element("article", "grid-effect-card");
      card.append(
        element("span", "", valueOr(effect.Name, "그리드 효과")),
        element("strong", "", `Lv.${valueOr(effect.Level, "-")}`),
      );
      const tooltip = cleanInline(effect.Tooltip);
      if (tooltip) card.title = tooltip;
      effectsContainer.append(card);
    });
  }

  slotsContainer.replaceChildren();
  if (slots.length === 0) {
    renderEmpty(slotsContainer, "장착된 아크 그리드 코어가 없습니다.");
    return;
  }

  slots.forEach((slot, slotIndex) => {
    const card = element("article", `grid-slot-card ${gradeClass(slot.Grade)}`.trim());
    const head = element("div", "grid-slot-head");
    const icon = element("div", "grid-core-icon");
    icon.append(imageElement(slot.Icon, ""));
    const copy = element("div", "grid-slot-copy");
    copy.append(
      element(
        "span",
        "",
        `${valueOr(slot.Grade, "등급 정보 없음")} · ${slotIndex + 1}번 슬롯`,
      ),
      element("strong", "", cleanInline(slot.Name) || `아크 그리드 코어 ${slotIndex + 1}`),
    );
    head.append(icon, copy, element("span", "grid-point", valueOr(slot.Point, "-")));
    card.append(head);

    const gems = toArray(slot.Gems);
    if (gems.length > 0) {
      const gemList = element("div", "grid-gem-list");
      gems.forEach((gem, gemIndex) => {
        const lines = tooltipLines(gem.Tooltip, [], 8);
        const gemTitle = lines[0] || `${slotIndex + 1}-${gemIndex + 1}번 젬`;
        const detailId = registerDetail({
          title: gemTitle,
          subtitle: `${valueOr(gem.Grade, "등급 정보 없음")} 아크 그리드 젬`,
          icon: gem.Icon,
          tooltip: gem.Tooltip,
          lines: [gem.IsActive ? "활성화됨" : "비활성화됨"],
        });
        const button = element(
          "button",
          `grid-gem-button ${gem.IsActive === false ? "is-inactive" : ""}`.trim(),
        );
        button.type = "button";
        button.dataset.detailId = detailId;
        button.title = gemTitle;
        button.append(
          imageElement(gem.Icon, ""),
          element("span", "", `${valueOr(gem.Grade, "젬")} ${gemIndex + 1}`),
        );
        gemList.append(button);
      });
      card.append(gemList);
    }

    const detailId = registerDetail({
      title: slot.Name,
      subtitle: `${valueOr(slot.Grade, "등급 정보 없음")} 코어 · ${valueOr(slot.Point, "-")}P`,
      icon: slot.Icon,
      tooltip: slot.Tooltip,
      lines: [`장착 젬 ${gems.length}개`],
    });
    card.append(createDetailButton(detailId, "코어 상세 효과"));
    slotsContainer.append(card);
  });
}

function renderCards(cardSection) {
  const effectsContainer = document.querySelector("[data-card-effects]");
  const cardsContainer = document.querySelector("[data-cards]");
  if (!effectsContainer || !cardsContainer) return;
  const cards = toArray(cardSection?.Cards);
  const effects = toArray(cardSection?.Effects);
  setSectionCount("cards", cards.length, "장");

  effectsContainer.replaceChildren();
  const activeEffects = effects
    .map((effect) => {
      const items = toArray(effect.Items);
      return {
        active: items.at(-1),
        items,
      };
    })
    .filter((effect) => effect.active);

  if (activeEffects.length === 0) {
    renderEmpty(effectsContainer, "활성화된 카드 세트 효과가 없습니다.");
  } else {
    activeEffects.forEach(({ active, items }) => {
      const row = element("button", "card-effect-row");
      row.type = "button";
      row.append(
        element("strong", "", cleanInline(active.Name) || "카드 세트 효과"),
        element("span", "", cleanInline(active.Description) || "-"),
      );
      const detailId = registerDetail({
        title: active.Name || "카드 세트 효과",
        subtitle: "활성 카드 세트 단계",
        lines: items.flatMap((item) => [
          cleanInline(item.Name),
          cleanInline(item.Description),
        ]),
      });
      row.dataset.detailId = detailId;
      row.setAttribute("aria-label", `${cleanInline(active.Name) || "카드 세트"} 전체 효과 보기`);
      effectsContainer.append(row);
    });
  }

  cardsContainer.replaceChildren();
  if (cards.length === 0) {
    renderEmpty(cardsContainer, "장착 카드 정보가 없습니다.");
    return;
  }

  cards.forEach((cardData) => {
    const card = element("article", "card-item");
    const imageWrap = element("div", "card-item-image");
    imageWrap.append(
      imageElement(cardData.Icon, ""),
      element(
        "span",
        "card-awake",
        `${valueOr(cardData.AwakeCount, 0)}/${valueOr(cardData.AwakeTotal, 0)}`,
      ),
    );
    const copy = element("div", "card-item-copy");
    copy.append(
      element("strong", "", cleanInline(cardData.Name) || "카드"),
      element("span", "", valueOr(cardData.Grade, "등급 정보 없음")),
    );
    const detailId = registerDetail({
      title: cardData.Name,
      subtitle: `${valueOr(cardData.Grade, "등급 정보 없음")} 카드`,
      icon: cardData.Icon,
      tooltip: cardData.Tooltip,
      lines: [
        `각성 ${valueOr(cardData.AwakeCount, 0)} / ${valueOr(cardData.AwakeTotal, 0)}`,
      ],
    });
    card.append(imageWrap, copy, createDetailButton(detailId, "카드 정보"));
    cardsContainer.append(card);
  });
}

function renderSkills(skills) {
  const container = document.querySelector("[data-skills]");
  if (!container) return;
  const allSkills = toArray(skills);
  const configured = allSkills.filter((skill) => Number(skill.Level) > 1);
  const visibleSkills = configured.length > 0 ? configured : allSkills;
  setSectionCount("skills", visibleSkills.length);
  container.replaceChildren();

  if (visibleSkills.length === 0) {
    renderEmpty(container, "전투 스킬 정보가 없습니다.");
    return;
  }

  visibleSkills.forEach((skill) => {
    const card = element("article", "skill-card");
    const icon = element("div", "skill-icon");
    icon.append(
      imageElement(skill.Icon, ""),
      element("span", "skill-level", valueOr(skill.Level, "-")),
    );
    const copy = element("div", "skill-copy");
    copy.append(
      element("span", "", cleanInline(skill.SkillType || skill.Type) || "전투 스킬"),
      element("strong", "", cleanInline(skill.Name) || "스킬"),
    );
    if (skill.Rune) {
      const rune = element("span", "skill-rune");
      if (skill.Rune.Icon) rune.append(imageElement(skill.Rune.Icon, ""));
      rune.append(
        document.createTextNode(
          `${valueOr(skill.Rune.Grade, "")} ${valueOr(cleanInline(skill.Rune.Name), "룬")}`.trim(),
        ),
      );
      copy.append(rune);
    }
    card.append(icon, copy);

    const selectedTripods = toArray(skill.Tripods).filter((tripod) => tripod.IsSelected);
    if (selectedTripods.length > 0) {
      const tripodList = element("ul", "tripod-list");
      selectedTripods.forEach((tripod) => {
        tripodList.append(element("li", "", cleanInline(tripod.Name) || "선택 트라이포드"));
      });
      card.append(tripodList);
    }

    const detailId = registerDetail({
      title: skill.Name,
      subtitle: `스킬 레벨 ${valueOr(skill.Level, "-")}`,
      icon: skill.Icon,
      tooltip: skill.Tooltip,
      lines: [
        skill.Rune
          ? `장착 룬: ${valueOr(skill.Rune.Grade, "")} ${valueOr(cleanInline(skill.Rune.Name), "")}`.trim()
          : "",
        ...selectedTripods.map(
          (tripod) => `트라이포드: ${cleanInline(tripod.Name) || "선택됨"}`,
        ),
      ],
    });
    card.append(createDetailButton(detailId, "스킬 상세 정보"));
    container.append(card);
  });
}

function renderAvatars(avatars) {
  const container = document.querySelector("[data-avatars]");
  if (!container) return;
  const items = toArray(avatars);
  setSectionCount("avatars", items.length);
  container.replaceChildren();

  if (items.length === 0) {
    renderEmpty(container, "장착 아바타 정보가 없습니다.");
    return;
  }

  items.forEach((avatar) => {
    const card = element("article", `avatar-card ${gradeClass(avatar.Grade)}`.trim());
    const icon = element("div", "avatar-icon");
    icon.append(imageElement(avatar.Icon, ""));
    const copy = element("div", "avatar-copy");
    copy.append(
      element("span", "", `${valueOr(avatar.Type, "아바타")} · ${valueOr(avatar.Grade, "등급 정보 없음")}`),
      element("strong", "", cleanInline(avatar.Name) || "아바타"),
    );
    const flags = element("div", "avatar-flags");
    if (avatar.IsSet) flags.append(element("span", "", "세트"));
    if (avatar.IsInner) flags.append(element("span", "", "내실"));
    if (flags.childElementCount > 0) copy.append(flags);
    card.append(icon, copy);

    const detailId = registerDetail({
      title: avatar.Name,
      subtitle: `${valueOr(avatar.Type, "아바타")} · ${valueOr(avatar.Grade, "등급 정보 없음")}`,
      icon: avatar.Icon,
      tooltip: avatar.Tooltip,
    });
    card.append(createDetailButton(detailId, "아바타 정보"));
    container.append(card);
  });
}

function renderCollectibles(collectibles) {
  const container = document.querySelector("[data-collectibles]");
  if (!container) return;
  const items = toArray(collectibles);
  setSectionCount("collectibles", items.length);
  container.replaceChildren();

  if (items.length === 0) {
    renderEmpty(container, "수집품 정보가 없습니다.");
    return;
  }

  items.forEach((collectible) => {
    const point = Number(collectible.Point) || 0;
    const maxPoint = Number(collectible.MaxPoint) || 0;
    const percent = maxPoint > 0 ? Math.min((point / maxPoint) * 100, 100) : 0;
    const card = element("article", "collectible-card");
    card.append(imageElement(collectible.Icon, ""));
    const copy = element("div", "collectible-copy");
    copy.append(element("strong", "", cleanInline(collectible.Type) || "수집품"));
    const progress = element("div", "collectible-progress");
    const bar = element("i");
    bar.style.width = `${percent}%`;
    progress.append(bar);
    copy.append(progress);
    card.append(copy, element("span", "", `${point} / ${maxPoint}`));
    container.append(card);
  });
}

function formatRosterPower(value) {
  if (value === null || value === undefined || value === "") return "확인 불가";
  const numeric = Number(String(value).replace(/,/g, "").trim());
  if (!Number.isFinite(numeric)) return cleanInline(value) || "확인 불가";
  return numeric.toLocaleString("ko-KR", {
    minimumFractionDigits: Number.isInteger(numeric) ? 0 : 2,
    maximumFractionDigits: 2,
  });
}

function setRosterState(state, message = "") {
  if (rosterLoading) rosterLoading.hidden = state !== "loading";
  if (rosterView) rosterView.hidden = state !== "ready";
  if (rosterError) rosterError.hidden = state !== "error";
  if (state === "error") {
    setText(
      "[data-roster-error-message]",
      message,
      "잠시 후 다시 시도해 주세요.",
    );
  }
}

function renderRosterSummary(response) {
  const data = response?.data || {};
  const characters = toArray(data.Characters);
  const serverName = cleanInline(data.ServerName) || "서버 정보 없음";
  const fetchedAt = formatFetchedAt(response?.fetched_at);
  const source = response?.stale
    ? "저장 데이터"
    : response?.cached
      ? "3분 캐시"
      : "Open API 갱신";

  setText("[data-roster-server]", serverName);
  setText("[data-roster-count]", `${characters.length}명`);
  setText(
    "[data-roster-meta]",
    `${characters.length}명 · ${source}${fetchedAt ? ` · ${fetchedAt}` : ""}`,
  );

  if (!rosterList) return;
  rosterList.replaceChildren();

  if (characters.length === 0) {
    renderEmpty(rosterList, "같은 서버의 원정대 캐릭터가 없습니다.");
    setRosterState("ready");
    return;
  }

  characters.forEach((character) => {
    const name = cleanInline(character.CharacterName) || "이름 정보 없음";
    const className = cleanInline(character.CharacterClassName) || "직업 정보 없음";
    const isCurrent =
      name.toLocaleLowerCase() === currentCharacterName.toLocaleLowerCase();
    const row = element(
      "a",
      `roster-character-row ${isCurrent ? "is-current" : ""}`.trim(),
    );
    row.href = `/character.html?name=${encodeURIComponent(name)}`;
    row.setAttribute(
      "aria-label",
      `${name} ${className}, 전투력 ${formatRosterPower(character.CombatPower)}`,
    );

    const mark = element(
      "span",
      "roster-class-mark",
      className.slice(0, 1) || "J",
    );
    const identity = element("span", "roster-character-identity");
    const nameLine = element("span", "roster-character-name");
    nameLine.append(element("strong", "", name));
    if (isCurrent) nameLine.append(element("em", "", "현재 캐릭터"));
    identity.append(nameLine, element("small", "", className));

    const power = element("span", "roster-character-power");
    power.append(
      element("small", "", "전투력"),
      element("strong", "", formatRosterPower(character.CombatPower)),
    );
    row.append(mark, identity, power);
    rosterList.append(row);
  });

  setRosterState("ready");
}

async function loadRosterSummary(name = currentCharacterName, fresh = false) {
  const normalized = normalizeCharacterName(name);
  if (!normalized || normalized.length < 2 || normalized.length > 20) return;
  if (!fresh && rosterLoadedName === normalized) {
    setRosterState("ready");
    return;
  }
  if (rosterRequestName === normalized) return;

  rosterAbortController?.abort();
  const controller = new AbortController();
  rosterAbortController = controller;
  rosterRequestName = normalized;
  setText("[data-roster-meta]", "원정대 확인 중");
  setRosterState("loading");

  const timeout = window.setTimeout(() => controller.abort(), 45000);
  try {
    const query = fresh ? "?fresh=true" : "";
    const response = await fetch(
      `${CHARACTER_API_BASE_URL}/api/roster/${encodeURIComponent(normalized)}/summary${query}`,
      {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || "원정대 정보를 불러오지 못했습니다.");
    }
    rosterLoadedName = normalized;
    renderRosterSummary(payload);
  } catch (error) {
    if (error?.name === "AbortError" && rosterRequestName !== normalized) return;
    console.error(error);
    const message =
      error?.name === "AbortError"
        ? "응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
        : error instanceof Error
          ? error.message
          : "원정대 조회 중 오류가 발생했습니다.";
    setText("[data-roster-meta]", "조회 실패");
    setRosterState("error", message);
  } finally {
    window.clearTimeout(timeout);
    if (rosterRequestName === normalized) rosterRequestName = "";
    if (rosterAbortController === controller) rosterAbortController = null;
  }
}

function renderCharacter(response) {
  const data = response?.data;
  const profile = data?.ArmoryProfile;
  if (!profile) throw new Error("캐릭터 프로필 정보가 없습니다.");

  detailRegistry.clear();
  detailSequence = 0;
  currentCharacterName = profile.CharacterName || currentCharacterName;
  if (characterInput) characterInput.value = currentCharacterName;

  const arkPassive = data.ArkPassive || {};
  renderProfile(profile, arkPassive, response);
  renderStats(profile, arkPassive);
  renderEquipment(data.ArmoryEquipment);
  renderGems(data.ArmoryGem || data.ArmoryGems);
  renderEngravings(data.ArmoryEngraving);
  renderArkPassive(arkPassive);
  renderArkGrid(data.ArkGrid);
  renderCards(data.ArmoryCard);
  renderSkills(data.ArmorySkills);
  renderAvatars(data.ArmoryAvatars);
  renderCollectibles(data.Collectibles);

  document.title = `${currentCharacterName} 캐릭터 상세 정보 — JLOA`;
  const description = document.querySelector('meta[name="description"]');
  if (description) {
    description.content =
      `${currentCharacterName}의 장비, 보석, 각인, 아크 패시브, 아크 그리드, 카드와 스킬 상세 정보`;
  }
  activateCharacterTab(activeCharacterTab, { updateHistory: false });
  updateCharacterHistory();
  saveRecentCharacter(currentCharacterName);
  showView();
}

async function loadCharacter(name, fresh = false) {
  const normalized = normalizeCharacterName(name);
  if (normalized.length < 2 || normalized.length > 20) {
    showMessage(
      "닉네임 형식을 확인해 주세요.",
      "캐릭터 닉네임은 2자 이상 20자 이하로 입력해야 합니다.",
    );
    characterInput?.focus();
    return;
  }

  currentCharacterName = normalized;
  if (characterInput) characterInput.value = normalized;
  if (!fresh) showLoading();
  if (refreshButton) refreshButton.disabled = true;

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 20000);

  try {
    const query = fresh ? "?fresh=true" : "";
    const response = await fetch(
      `${CHARACTER_API_BASE_URL}/api/character/${encodeURIComponent(normalized)}${query}`,
      {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 404) {
        throw new Error("입력한 닉네임의 캐릭터를 찾을 수 없습니다.");
      }
      throw new Error(payload.error || "캐릭터 정보를 불러오지 못했습니다.");
    }
    renderCharacter(payload);
  } catch (error) {
    console.error(error);
    const message =
      error?.name === "AbortError"
        ? "응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
        : error instanceof Error
          ? error.message
          : "캐릭터 조회 중 오류가 발생했습니다.";
    showMessage("캐릭터 정보를 불러오지 못했습니다.", message);
  } finally {
    window.clearTimeout(timeout);
    if (refreshButton) refreshButton.disabled = false;
  }
}

if (characterForm && characterInput) {
  characterForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const nextName = normalizeCharacterName(characterInput.value);
    if (nextName === currentCharacterName) {
      loadCharacter(nextName);
      return;
    }
    if (nextName.length < 2 || nextName.length > 20) {
      showMessage(
        "닉네임 형식을 확인해 주세요.",
        "캐릭터 닉네임은 2자 이상 20자 이하로 입력해야 합니다.",
      );
      return;
    }
    window.location.assign(`/character.html?name=${encodeURIComponent(nextName)}`);
  });
}

if (refreshButton) {
  refreshButton.addEventListener("click", () => {
    if (currentCharacterName) loadCharacter(currentCharacterName, true);
  });
}

if (rosterRetryButton) {
  rosterRetryButton.addEventListener("click", () => {
    if (currentCharacterName) loadRosterSummary(currentCharacterName);
  });
}

characterTabButtons.forEach((button, index) => {
  button.addEventListener("click", () => {
    activateCharacterTab(button.dataset.characterTab, { updateHistory: true });
  });

  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();

    let nextIndex = index;
    if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + characterTabButtons.length) % characterTabButtons.length;
    } else if (event.key === "ArrowRight") {
      nextIndex = (index + 1) % characterTabButtons.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = characterTabButtons.length - 1;
    }

    activateCharacterTab(characterTabButtons[nextIndex].dataset.characterTab, {
      updateHistory: true,
      focus: true,
    });
  });
});

characterTabOpeners.forEach((opener) => {
  opener.addEventListener("click", (event) => {
    event.preventDefault();
    activateCharacterTab(opener.dataset.openCharacterTab, {
      updateHistory: true,
      scroll: true,
    });
  });
});

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-detail-id]");
  if (trigger) openDetail(trigger.dataset.detailId);
});

if (itemDialog) {
  itemDialog.addEventListener("click", (event) => {
    if (event.target === itemDialog) itemDialog.close();
  });
}

const initialName = initialParams.get("name") || initialParams.get("character");
activateCharacterTab(activeCharacterTab, { updateHistory: false });
if (initialName) {
  loadCharacter(initialName);
} else {
  showMessage(
    "확인할 캐릭터를 검색해 주세요.",
    "상단 검색창에 로스트아크 캐릭터 닉네임을 입력하면 상세 정보를 확인할 수 있습니다.",
  );
}
