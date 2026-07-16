/*
 * JLOA 웹사이트 배포 설정
 *
 * 새 버전을 배포할 때 RELEASE의 값을 모두 새 파일에 맞게 변경하세요.
 * 새 설치 파일은 SHA-256과 VirusTotal 링크도 달라집니다.
 */
const RELEASE = Object.freeze({
  version: "0.0.0",
  fileName: "JLOA-Setup-0.0.0-x64.exe",
  sha256:
    "02bb13df1f26e650b11b85c2a11ab90d496712f81ab6271580bafcf9656c33ff",
  virusTotalUrl:
    "https://www.virustotal.com/gui/file/02bb13df1f26e650b11b85c2a11ab90d496712f81ab6271580bafcf9656c33ff/detection",
  scanDate: "2026-07-14",
  scanSummary: "검사 시점 기준 66개 보안 엔진에서 탐지 없음",
});

const SUPPORT_URL = "https://open.kakao.com/o/sCe2zYDi";

const DOWNLOAD_URL = `/downloads/${RELEASE.fileName}`;
const CHECKSUM_URL = `${DOWNLOAD_URL}.sha256`;

const RELEASE_NOTES_URL = "/release-notes";
const PRIVACY_URL = "/privacy";
const AD_POLICY_URL = "/ads-policy";

const releaseMeta =
  `Windows 10/11 · 64비트 · v${RELEASE.version}`;

function setText(selector, value) {
  document.querySelectorAll(selector).forEach((element) => {
    element.textContent = value;
  });
}

function setHref(selector, value) {
  document.querySelectorAll(selector).forEach((element) => {
    element.href = value;
  });
}

/* 다운로드 링크 */
document.querySelectorAll("[data-download-link]").forEach((link) => {
  link.href = DOWNLOAD_URL;
  link.download = RELEASE.fileName;
});

/* 버전 정보 */
setText("[data-release-meta]", releaseMeta);
setText("[data-version]", `v${RELEASE.version}`);
setText("[data-file-name]", RELEASE.fileName);

/* 보안 검증 정보 */
setText("[data-sha256]", RELEASE.sha256);
setText("[data-scan-summary]", RELEASE.scanSummary);
setText("[data-scan-date]", `검사일: ${RELEASE.scanDate}`);

/* 정책 및 외부 링크 */
setHref("[data-release-link]", RELEASE_NOTES_URL);
setHref("[data-privacy-link]", PRIVACY_URL);
setHref("[data-ad-policy-link]", AD_POLICY_URL);
setHref("[data-virustotal-link]", RELEASE.virusTotalUrl);

/* SHA-256 파일 다운로드 */
document.querySelectorAll("[data-checksum-link]").forEach((link) => {
  link.href = CHECKSUM_URL;
  link.download = `${RELEASE.fileName}.sha256`;
});

/* 문의 메일 */
document.querySelectorAll("[data-support-link]").forEach((link) => {
  link.href = SUPPORT_URL;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
});

/* 연도 */
setText("[data-year]", String(new Date().getFullYear()));

/* SHA-256 복사 */
const copyButton = document.querySelector("[data-copy-sha256]");
const copyStatus = document.querySelector("[data-copy-status]");

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textArea = document.createElement("textarea");

  textArea.value = text;
  textArea.setAttribute("readonly", "");
  textArea.style.position = "fixed";
  textArea.style.opacity = "0";

  document.body.appendChild(textArea);
  textArea.select();

  const copied = document.execCommand("copy");
  textArea.remove();

  if (!copied) {
    throw new Error("클립보드 복사 실패");
  }
}

if (copyButton) {
  copyButton.addEventListener("click", async () => {
    try {
      await copyText(RELEASE.sha256);

      if (copyStatus) {
        copyStatus.textContent =
          "SHA-256을 클립보드에 복사했습니다.";
      }
    } catch (error) {
      console.error(error);

      if (copyStatus) {
        copyStatus.textContent =
          "자동 복사에 실패했습니다. 해시를 직접 선택해 복사해 주세요.";
      }
    }
  });
}

/* 스크롤 헤더 */
const header = document.querySelector("[data-header]");

if (header) {
  const updateHeader = () => {
    header.classList.toggle(
      "is-scrolled",
      window.scrollY > 8,
    );
  };

  updateHeader();

  window.addEventListener("scroll", updateHeader, {
    passive: true,
  });
}

/* 모바일 메뉴 */
const menuToggle = document.querySelector("[data-menu-toggle]");
const nav = document.querySelector("[data-nav]");
const mobileNavigation = window.matchMedia("(max-width: 860px)");

const setMenuState = (isOpen) => {
  if (!menuToggle || !nav) return;

  menuToggle.setAttribute("aria-expanded", String(isOpen));
  menuToggle.setAttribute("aria-label", isOpen ? "메뉴 닫기" : "메뉴 열기");
  nav.classList.toggle("is-open", isOpen);
  document.body.classList.toggle("menu-open", isOpen);
};

if (menuToggle && nav) {
  setMenuState(false);

  menuToggle.addEventListener("click", () => {
    const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
    setMenuState(!isOpen);
  });

  nav.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setMenuState(false));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setMenuState(false);
      menuToggle.focus();
    }
  });

  document.addEventListener("click", (event) => {
    if (
      menuToggle.getAttribute("aria-expanded") === "true" &&
      !nav.contains(event.target) &&
      !menuToggle.contains(event.target)
    ) {
      setMenuState(false);
    }
  });

  const handleViewportChange = (event) => {
    if (!event.matches) setMenuState(false);
  };

  if (typeof mobileNavigation.addEventListener === "function") {
    mobileNavigation.addEventListener("change", handleViewportChange);
  } else {
    mobileNavigation.addListener(handleViewportChange);
  }
}

/* 화면 등장 애니메이션 */
const revealElements =
  document.querySelectorAll(".reveal");

if (
  "IntersectionObserver" in window &&
  !window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches
) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    {
      threshold: 0.12,
    },
  );

  revealElements.forEach((element) => {
    observer.observe(element);
  });
} else {
  revealElements.forEach((element) => {
    element.classList.add("is-visible");
  });
}

/* 페이지 내부 이동 시 #faq 같은 해시를 주소창에 표시하지 않음 */
const reducedMotion = window.matchMedia(
  "(prefers-reduced-motion: reduce)",
).matches;

function scrollToSection(sectionId) {
  const target = document.getElementById(sectionId);

  if (!target) {
    return;
  }

  target.scrollIntoView({
    behavior: reducedMotion ? "auto" : "smooth",
    block: "start",
  });

  // 주소창에서 #faq, #features 등을 제거합니다.
  history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}`,
  );
}

document.querySelectorAll('a[href^="#"]').forEach((link) => {
  link.addEventListener("click", (event) => {
    const href = link.getAttribute("href");

    // href="#"처럼 대상이 없는 링크는 제외합니다.
    if (!href || href === "#") {
      return;
    }

    const sectionId = decodeURIComponent(href.slice(1));
    const target = document.getElementById(sectionId);

    if (!target) {
      return;
    }

    event.preventDefault();
    scrollToSection(sectionId);
  });
});

/*
 * 다른 페이지에서 /#faq처럼 들어온 경우:
 * 해당 위치로 이동한 뒤 주소창에서 해시를 제거합니다.
 */
window.addEventListener("load", () => {
  if (!window.location.hash) {
    return;
  }

  const sectionId = decodeURIComponent(
    window.location.hash.slice(1),
  );

  requestAnimationFrame(() => {
    scrollToSection(sectionId);
  });
});
