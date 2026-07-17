/* JLOA 계산 엔진 런타임 — Pyodide 부트 + 엔진 파일 적재 + JS↔파이썬 브리지.
 *
 * 엔진(website/py/)은 데스크탑 앱과 동일한 파이썬 파일이다
 * (scripts/sync_web_engines.py 로 갱신). stdlib 만 쓰므로 Pyodide core 만
 * 내려받으면 되고(최초 1회 후 브라우저 캐시), 계산 결과는 앱과 100% 일치한다.
 *
 * 사용:
 *   JloaPy.boot()                 — 엔진 준비 (lazy, 중복 호출 안전)
 *   JloaPy.call("app.features.gem_craft:success_probability", {...args})
 *     → 파이썬 함수를 키워드 인자로 호출, 결과를 JSON 으로 돌려받는다
 *   JloaPy.bootVision()           — 화면 인식 추가 준비 (numpy+opencv, 수십 MB)
 *   JloaPy.callFrame("web_glue:vision_gem", imageData, {...args})
 *     → 캔버스 프레임(RGBA)을 판독기에 넘긴다
 * 상태는 [data-py-status] 요소에 반영된다 (data-state: loading|ready|error).
 */
const JloaPy = (() => {
  const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
  let bootPromise = null;
  let visionPromise = null;

  function setStatus(text, state) {
    document.querySelectorAll("[data-py-status]").forEach((el) => {
      el.textContent = text;
      if (state) el.dataset.state = state;
      else delete el.dataset.state;
    });
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error("스크립트 로드 실패: " + src));
      document.head.appendChild(tag);
    });
  }

  async function fetchIntoFs(py, rel) {
    const resp = await fetch("/py/" + encodeURI(rel));
    if (!resp.ok) throw new Error("엔진 파일 로드 실패: " + rel);
    const data = new Uint8Array(await resp.arrayBuffer());
    const full = "/jloa/" + rel;
    py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
    py.FS.writeFile(full, data);
  }

  async function doBoot() {
    setStatus("계산 엔진 내려받는 중…", "loading");
    await loadScript(PYODIDE_BASE + "pyodide.js");
    const py = await loadPyodide({ indexURL: PYODIDE_BASE });

    setStatus("엔진 파일 적재 중…", "loading");
    const manifest = await (await fetch("/py/manifest.json", { cache: "no-cache" })).json();
    await Promise.all(manifest.files.map((rel) => fetchIntoFs(py, rel)));

    py.runPython('import sys; sys.path.insert(0, "/jloa")');
    // 임포트 스모크 — 4개 엔진이 브라우저에서 그대로 뜨는지 확인
    py.runPython(
      "import app.features.normal_refine, app.features.advanced_refine, " +
      "app.features.gem_craft, app.features.crit_rate"
    );
    setStatus("계산 엔진 준비 완료", "ready");
    return py;
  }

  function boot() {
    if (!bootPromise) {
      bootPromise = doBoot().catch((err) => {
        bootPromise = null;
        setStatus("엔진 로드 실패 — 새로고침 후 다시 시도해 주세요", "error");
        throw err;
      });
    }
    return bootPromise;
  }

  /* target: "모듈경로:함수명", args: 키워드 인자 객체 (JSON 직렬화 가능해야 함) */
  async function call(target, args) {
    const py = await boot();
    const [modName, fnName] = target.split(":");
    py.globals.set("_jloa_target_mod", modName);
    py.globals.set("_jloa_target_fn", fnName);
    py.globals.set("_jloa_args_json", JSON.stringify(args || {}));
    const out = py.runPython(
      "import importlib, json\n" +
      "json.dumps(getattr(importlib.import_module(_jloa_target_mod), _jloa_target_fn)" +
      "(**json.loads(_jloa_args_json)), ensure_ascii=False)"
    );
    return JSON.parse(out);
  }

  /* 자유형 파이썬 실행 (마지막 식이 JSON 문자열이어야 함) — 글루 코드용 */
  async function runJson(code, args) {
    const py = await boot();
    py.globals.set("_jloa_args_json", JSON.stringify(args || {}));
    return JSON.parse(py.runPython(code));
  }

  /* 화면 인식 준비 — numpy+opencv 패키지와 판독기·템플릿(vision_files) 추가 로드 */
  function bootVision() {
    if (!visionPromise) {
      visionPromise = (async () => {
        const py = await boot();
        setStatus("인식 엔진 내려받는 중… (최초 1회)", "loading");
        await py.loadPackage(["numpy", "opencv-python"]);
        const manifest = await (await fetch("/py/manifest.json", { cache: "no-cache" })).json();
        await Promise.all((manifest.vision_files || []).map((rel) => fetchIntoFs(py, rel)));
        py.runPython("import app.vision");
        setStatus("화면 인식 준비 완료", "ready");
        return py;
      })().catch((err) => {
        visionPromise = null;
        setStatus("인식 엔진 로드 실패 — 새로고침 후 다시 시도해 주세요", "error");
        throw err;
      });
    }
    return visionPromise;
  }

  /* 캔버스 ImageData(RGBA)를 판독 함수에 전달. target 함수 시그니처:
     fn(frame_bytes, width, height, **args) */
  async function callFrame(target, imageData, args) {
    const py = await bootVision();
    const [modName, fnName] = target.split(":");
    py.globals.set("_jloa_target_mod", modName);
    py.globals.set("_jloa_target_fn", fnName);
    py.globals.set("_jloa_args_json", JSON.stringify(args || {}));
    py.globals.set("_jloa_frame", new Uint8Array(imageData.data.buffer));
    py.globals.set("_jloa_frame_w", imageData.width);
    py.globals.set("_jloa_frame_h", imageData.height);
    const out = py.runPython(
      "import importlib, json\n" +
      "json.dumps(getattr(importlib.import_module(_jloa_target_mod), _jloa_target_fn)" +
      "(_jloa_frame, _jloa_frame_w, _jloa_frame_h, **json.loads(_jloa_args_json)), ensure_ascii=False)"
    );
    return JSON.parse(out);
  }

  return { boot, call, runJson, bootVision, callFrame };
})();
