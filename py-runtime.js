/* JLOA 계산 엔진 런타임 — Web Worker(py-worker.js)의 Pyodide 를 호출하는 브리지.
 *
 * 엔진 로드·화면 판독 계산은 전부 워커에서 실행돼 UI 가 멈추지 않는다
 * (loatto 와 같은 구조 — 2026-07-18 랙 개선). 엔진(py/)은 데스크탑 앱과
 * 동일한 파이썬 파일이며 scripts/sync_web_engines.py 로 갱신한다.
 *
 * 사용 (API 는 워커 도입 전과 동일):
 *   JloaPy.boot()                 — 엔진 준비 (lazy, 중복 호출 안전)
 *   JloaPy.call("web_glue:gem_calc", {...})     — JSON 인자 호출
 *   JloaPy.bootVision()           — 화면 인식 준비 (numpy+opencv, 수십 MB)
 *   JloaPy.callFrame("web_glue:vision_gem", imageData, {...})
 * 상태는 [data-py-status] 요소에 반영된다 (data-state: loading|ready|error).
 */
const JloaPy = (() => {
  const WORKER_URL = "/py-worker.js?v=20260718-worker";
  let worker = null;
  let nextId = 1;
  const pending = new Map();

  function setStatus(text, state) {
    document.querySelectorAll("[data-py-status]").forEach((el) => {
      el.textContent = text;
      if (state) el.dataset.state = state;
      else delete el.dataset.state;
    });
  }

  function ensureWorker() {
    if (!worker) {
      worker = new Worker(WORKER_URL);
      worker.onmessage = (event) => {
        const msg = event.data;
        if (msg.op === "status") { setStatus(msg.text, msg.state); return; }
        const req = pending.get(msg.id);
        if (!req) return;
        pending.delete(msg.id);
        if (msg.ok) req.resolve(msg.result);
        else req.reject(new Error(msg.error));
      };
      worker.onerror = (event) => {
        const err = new Error("파이썬 워커 오류: " + (event.message || "알 수 없음"));
        pending.forEach((req) => req.reject(err));
        pending.clear();
        setStatus("엔진 오류 — 새로고침 후 다시 시도해 주세요", "error");
      };
    }
    return worker;
  }

  function request(msg, transfer) {
    return new Promise((resolve, reject) => {
      msg.id = nextId++;
      pending.set(msg.id, { resolve: resolve, reject: reject });
      ensureWorker().postMessage(msg, transfer || []);
    });
  }

  function boot() {
    return request({ op: "boot" });
  }

  function bootVision() {
    return request({ op: "bootVision" });
  }

  function call(target, args) {
    return request({ op: "call", target: target, argsJson: JSON.stringify(args || {}) });
  }

  function runJson(code, args) {
    return request({ op: "runJson", code: code, argsJson: JSON.stringify(args || {}) });
  }

  /* 캔버스 ImageData(RGBA)를 판독 함수에 전달. 사본을 만들어 zero-copy 전송하므로
     원본 imageData 는 미리보기·프레임 저장에 계속 쓸 수 있다. */
  function callFrame(target, imageData, args) {
    const copy = new Uint8Array(imageData.data).buffer;
    return request({
      op: "callFrame", target: target,
      argsJson: JSON.stringify(args || {}),
      buffer: copy, width: imageData.width, height: imageData.height,
    }, [copy]);
  }

  return { boot, call, runJson, bootVision, callFrame };
})();
