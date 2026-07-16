const WEB_API_BASE_URL = "https://api.jloa.cloud";
const RECENT_SEARCH_KEY = "jloa.recentCharacters";
const MAX_RECENT_SEARCHES = 5;

const characterForm = document.querySelector("[data-character-form]");
const characterInput = document.querySelector("[data-character-input]");
const characterSubmit = document.querySelector("[data-character-submit]");
const characterStatus = document.querySelector("[data-character-status]");
const characterResult = document.querySelector("[data-character-result]");
const characterRefresh = document.querySelector("[data-character-refresh]");
const recentSearches = document.querySelector("[data-recent-searches]");
const recentSearchList = document.querySelector("[data-recent-search-list]");
const webToast = document.querySelector("[data-web-toast]");

let currentCharacterName = "";
let toastTimer = null;

function setCharacterText(selector, value, fallback = "-") {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = value || fallback;
  }
}

function setSearchStatus(message, state = "") {
  if (!characterStatus) return;
  characterStatus.textContent = message;
  characterStatus.classList.toggle("is-error", state === "error");
  characterStatus.classList.toggle("is-success", state === "success");
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

function getRecentSearches() {
  try {
    const stored = JSON.parse(localStorage.getItem(RECENT_SEARCH_KEY) || "[]");
    return Array.isArray(stored)
      ? stored.filter((name) => typeof name === "string").slice(0, MAX_RECENT_SEARCHES)
      : [];
  } catch {
    return [];
  }
}

function saveRecentSearch(name) {
  const normalized = name.trim();
  const next = [
    normalized,
    ...getRecentSearches().filter(
      (item) => item.toLocaleLowerCase() !== normalized.toLocaleLowerCase(),
    ),
  ].slice(0, MAX_RECENT_SEARCHES);

  try {
    localStorage.setItem(RECENT_SEARCH_KEY, JSON.stringify(next));
  } catch {
    // 저장소 사용이 차단된 환경에서도 검색 자체는 계속 동작한다.
  }
  renderRecentSearches();
}

function renderRecentSearches() {
  if (!recentSearches || !recentSearchList) return;
  const names = getRecentSearches();
  recentSearchList.replaceChildren();
  recentSearches.hidden = names.length === 0;

  names.forEach((name) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = name;
    button.addEventListener("click", () => {
      if (characterInput) characterInput.value = name;
      loadCharacter(name);
    });
    recentSearchList.appendChild(button);
  });
}

function updateCharacterQuery(name) {
  const url = new URL(window.location.href);
  url.searchParams.set("character", name);
  history.replaceState(null, "", `${url.pathname}${url.search}`);
}

function renderCharacter(response) {
  const profile = response.data?.ArmoryProfile;
  if (!profile) {
    throw new Error("캐릭터 프로필 정보가 없습니다.");
  }

  currentCharacterName = profile.CharacterName || currentCharacterName;
  setCharacterText("[data-character-server]", profile.ServerName, "서버 정보 없음");
  setCharacterText("[data-character-name]", profile.CharacterName, "이름 정보 없음");
  setCharacterText("[data-character-class]", profile.CharacterClassName, "클래스 정보 없음");
  setCharacterText("[data-character-item-level]", profile.ItemAvgLevel);
  setCharacterText("[data-character-level]", profile.CharacterLevel);
  setCharacterText("[data-character-expedition]", profile.ExpeditionLevel);
  setCharacterText("[data-character-pvp]", profile.PvpGradeName, "등급 없음");
  setCharacterText("[data-character-title]", profile.Title, "칭호 없음");
  setCharacterText("[data-character-guild]", profile.GuildName, "길드 미가입");

  const image = document.querySelector("[data-character-image]");
  if (image) {
    if (profile.CharacterImage) {
      image.src = profile.CharacterImage;
      image.alt = `${profile.CharacterName} 캐릭터 이미지`;
      image.classList.remove("is-placeholder");
    } else {
      image.src = "/jloa-icon.png";
      image.alt = "";
      image.classList.add("is-placeholder");
    }
  }

  const sourceChip = document.querySelector("[data-character-source]");
  const fetchedAt = formatFetchedAt(response.fetched_at);
  let sourceMessage = "로스트아크 Open API에서 새로 조회했습니다.";
  let sourceLabel = "OPEN API 최신 정보";
  let sourceClass = "";

  if (response.stale) {
    sourceMessage = "Open API 갱신 실패로 저장된 정보를 표시합니다.";
    sourceLabel = "저장 데이터 · 갱신 지연";
    sourceClass = "is-stale";
  } else if (response.cached) {
    sourceMessage = "JLOA DB의 최근 3분 이내 정보를 표시합니다.";
    sourceLabel = "JLOA 스마트 캐시";
    sourceClass = "is-cache";
  }

  if (sourceChip) {
    sourceChip.textContent = sourceLabel;
    sourceChip.classList.remove("is-cache", "is-stale");
    if (sourceClass) sourceChip.classList.add(sourceClass);
  }

  setCharacterText(
    "[data-character-cache-meta]",
    `${sourceMessage}${fetchedAt ? ` · 조회 시각 ${fetchedAt}` : ""}`,
    sourceMessage,
  );

  if (characterResult) {
    characterResult.hidden = false;
  }
  saveRecentSearch(currentCharacterName);
  updateCharacterQuery(currentCharacterName);
}

async function loadCharacter(name, fresh = false) {
  if (!characterStatus || !characterRefresh || !characterSubmit) return;
  const normalized = name.trim();

  if (normalized.length < 2 || normalized.length > 20) {
    setSearchStatus("닉네임을 2자 이상 20자 이하로 입력해 주세요.", "error");
    characterInput?.focus();
    return;
  }

  setSearchStatus(
    fresh
      ? "로스트아크 Open API에서 최신 정보를 가져오는 중입니다."
      : "JLOA 서버에서 캐릭터 정보를 확인하는 중입니다.",
  );
  characterSubmit.disabled = true;
  characterRefresh.disabled = true;

  try {
    const query = fresh ? "?fresh=true" : "";
    const response = await fetch(
      `${WEB_API_BASE_URL}/api/character/${encodeURIComponent(normalized)}${query}`,
      { headers: { Accept: "application/json" } },
    );
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(payload.error || "캐릭터 정보를 불러오지 못했습니다.");
    }

    renderCharacter(payload);
    setSearchStatus(
      fresh ? "최신 정보로 갱신했습니다." : "캐릭터 정보를 불러왔습니다.",
      "success",
    );

    requestAnimationFrame(() => {
      characterResult?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "center",
      });
    });
  } catch (error) {
    console.error(error);
    setSearchStatus(
      error instanceof Error
        ? error.message
        : "캐릭터 검색 중 오류가 발생했습니다.",
      "error",
    );
  } finally {
    characterSubmit.disabled = false;
    characterRefresh.disabled = false;
  }
}

function showToast(message) {
  if (!webToast) return;
  webToast.textContent = message;
  webToast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    webToast.hidden = true;
  }, 2600);
}

if (characterForm && characterInput && characterRefresh) {
  characterForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadCharacter(characterInput.value);
  });

  characterRefresh.addEventListener("click", () => {
    if (currentCharacterName) {
      loadCharacter(currentCharacterName, true);
    }
  });

  renderRecentSearches();

  const initialName = new URLSearchParams(window.location.search).get("character");
  if (initialName) {
    characterInput.value = initialName;
    loadCharacter(initialName);
  }
}

document.querySelectorAll("[data-coming-soon]").forEach((button) => {
  button.addEventListener("click", () => {
    showToast(`${button.dataset.comingSoon} 기능은 계산 엔진과 함께 순차적으로 공개됩니다.`);
  });
});
