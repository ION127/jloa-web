/* JLOA 백엔드 API 소비 — 시세(/api/prices/refine) + 공용 요청 헬퍼.
 *
 * 백엔드는 두 런타임이 있다 (legacy SQLite / 분산 Redis). 계약은 동일하게
 * {data, fetched_at, cached, stale} 이지만, 분산 모드는 **콜드 조회에서
 * 202 + {data:null, pending:true, retry_after}** 를 줄 수 있다 — request() 가
 * retry_after 만큼 기다렸다 재시도해 어느 모드에서든 데이터를 돌려준다.
 *
 * 사용:
 *   const body = await JloaPrices.request("/api/character/닉");  — 공용
 *   const { prices, fetchedAt, stale } = await JloaPrices.fetch(); — 시세
 *   JloaPrices.applyBadge();   — [data-price-badge] 에 "시세 기준 …" 표시
 */
const JloaPrices = (() => {
  const API_BASE = "https://api.jloa.cloud";
  let cached = null;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function request(path) {
    let pendingMessage = "서버가 데이터를 준비 중입니다";
    for (let attempt = 0; attempt < 4; attempt++) {
      const resp = await fetch(API_BASE + path);
      let body = null;
      try { body = await resp.json(); } catch (err) { /* 비 JSON 응답 */ }
      if (resp.status === 202 || (body && body.pending)) {
        const wait = (body && body.retry_after) || 2;
        await sleep(wait * 1000);
        continue;
      }
      if (!resp.ok) {
        throw new Error((body && body.error) || ("조회 실패 (" + resp.status + ")"));
      }
      if (!body || body.data == null) {
        throw new Error((body && body.error) || "서버 응답이 비어 있습니다.");
      }
      return body;
    }
    throw new Error(pendingMessage + " — 잠시 후 다시 시도해 주세요.");
  }

  async function fetchPrices(force) {
    if (cached && !force) return cached;
    const body = await request("/api/prices/refine");
    cached = {
      prices: body.data,
      fetchedAt: body.fetched_at,
      stale: Boolean(body.stale),
    };
    return cached;
  }

  function badgeText(fetchedAtIso, stale) {
    const at = new Date(fetchedAtIso);
    const hh = String(at.getHours()).padStart(2, "0");
    const mm = String(at.getMinutes()).padStart(2, "0");
    let text = "시세 기준 " + hh + ":" + mm + " · 15분마다 갱신";
    if (stale) text += " (일시적으로 이전 시세)";
    return text;
  }

  async function applyBadge() {
    const badges = document.querySelectorAll("[data-price-badge]");
    if (!badges.length) return;
    try {
      const { fetchedAt, stale } = await fetchPrices();
      badges.forEach((el) => {
        el.textContent = badgeText(fetchedAt, stale);
        el.dataset.state = stale ? "loading" : "ready";
      });
    } catch (err) {
      badges.forEach((el) => {
        el.textContent = "시세를 불러오지 못했습니다";
        el.dataset.state = "error";
      });
    }
  }

  return { fetch: fetchPrices, applyBadge, request, API_BASE };
})();
