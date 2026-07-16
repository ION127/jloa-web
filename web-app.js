const RECENT_SEARCH_KEY = "jloa.recentCharacters";
const MAX_RECENT_SEARCHES = 5;

const characterForm = document.querySelector("[data-character-form]");
const characterInput = document.querySelector("[data-character-input]");
const characterStatus = document.querySelector("[data-character-status]");
const recentSearches = document.querySelector("[data-recent-searches]");
const recentSearchList = document.querySelector("[data-recent-search-list]");
const webToast = document.querySelector("[data-web-toast]");

let toastTimer = null;

function setSearchStatus(message, state = "") {
  if (!characterStatus) return;
  characterStatus.textContent = message;
  characterStatus.classList.toggle("is-error", state === "error");
  characterStatus.classList.toggle("is-success", state === "success");
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
    // 저장소가 차단되어도 검색은 계속 동작한다.
  }
  renderRecentSearches();
}

function openCharacterPage(name) {
  const normalized = String(name || "").trim();
  if (normalized.length < 2 || normalized.length > 20) {
    setSearchStatus("닉네임을 2자 이상 20자 이하로 입력해 주세요.", "error");
    characterInput?.focus();
    return;
  }

  saveRecentSearch(normalized);
  setSearchStatus("캐릭터 정보를 여는 중입니다.", "success");
  window.location.assign(`/character.html?name=${encodeURIComponent(normalized)}`);
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
    button.addEventListener("click", () => openCharacterPage(name));
    recentSearchList.append(button);
  });
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

if (characterForm && characterInput) {
  characterForm.addEventListener("submit", (event) => {
    event.preventDefault();
    openCharacterPage(characterInput.value);
  });

  renderRecentSearches();

  const params = new URLSearchParams(window.location.search);
  const initialName = params.get("character") || params.get("name");
  if (initialName) {
    characterInput.value = initialName;
    openCharacterPage(initialName);
  }
}

document.querySelectorAll("[data-coming-soon]").forEach((button) => {
  button.addEventListener("click", () => {
    showToast(`${button.dataset.comingSoon} 기능은 계산 엔진과 함께 순차적으로 공개됩니다.`);
  });
});
