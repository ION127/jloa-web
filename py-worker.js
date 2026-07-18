/* JLOA 파이썬 워커 — Pyodide 를 메인 스레드 밖에서 실행한다.
 *
 * 엔진 로드(수십 MB)와 화면 판독 계산이 UI 를 멈추지 않게 하는 것이 목적.
 * py-runtime.js 가 이 워커를 띄우고 {id, op, …} 메시지로 호출한다.
 * 진행 상태는 {op:"status", text, state} 로 역방향 통지.
 */
const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
let py = null;
let bootPromise = null;
let visionPromise = null;

function status(text, state) {
  self.postMessage({ op: "status", text: text, state: state });
}

async function fetchIntoFs(rel) {
  const resp = await fetch("/py/" + encodeURI(rel));
  if (!resp.ok) throw new Error("엔진 파일 로드 실패: " + rel);
  const data = new Uint8Array(await resp.arrayBuffer());
  const full = "/jloa/" + rel;
  py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
  py.FS.writeFile(full, data);
}

function boot() {
  if (!bootPromise) {
    bootPromise = (async () => {
      status("계산 엔진 내려받는 중…", "loading");
      importScripts(PYODIDE_BASE + "pyodide.js");
      py = await loadPyodide({ indexURL: PYODIDE_BASE });
      status("엔진 파일 적재 중…", "loading");
      const manifest = await (await fetch("/py/manifest.json", { cache: "no-cache" })).json();
      await Promise.all(manifest.files.map(fetchIntoFs));
      py.runPython('import sys; sys.path.insert(0, "/jloa")');
      py.runPython(
        "import app.features.normal_refine, app.features.advanced_refine, " +
        "app.features.gem_craft, app.features.crit_rate"
      );
      status("계산 엔진 준비 완료", "ready");
    })().catch((err) => {
      bootPromise = null;
      status("엔진 로드 실패 — 새로고침 후 다시 시도해 주세요", "error");
      throw err;
    });
  }
  return bootPromise;
}

function bootVision() {
  if (!visionPromise) {
    visionPromise = (async () => {
      await boot();
      status("인식 엔진 내려받는 중… (최초 1회)", "loading");
      await py.loadPackage(["numpy", "opencv-python"]);
      const manifest = await (await fetch("/py/manifest.json", { cache: "no-cache" })).json();
      await Promise.all((manifest.vision_files || []).map(fetchIntoFs));
      py.runPython("import app.vision");
      status("화면 인식 준비 완료", "ready");
    })().catch((err) => {
      visionPromise = null;
      status("인식 엔진 로드 실패 — 새로고침 후 다시 시도해 주세요", "error");
      throw err;
    });
  }
  return visionPromise;
}

const CALL_SNIPPET =
  "import importlib, json\n" +
  "json.dumps(getattr(importlib.import_module(_jloa_target_mod), _jloa_target_fn)" +
  "(**json.loads(_jloa_args_json)), ensure_ascii=False)";

const FRAME_SNIPPET =
  "import importlib, json\n" +
  "json.dumps(getattr(importlib.import_module(_jloa_target_mod), _jloa_target_fn)" +
  "(_jloa_frame, _jloa_frame_w, _jloa_frame_h, **json.loads(_jloa_args_json)), ensure_ascii=False)";

self.onmessage = async (event) => {
  const msg = event.data;
  try {
    let result = null;
    if (msg.op === "boot") {
      await boot();
    } else if (msg.op === "bootVision") {
      await bootVision();
    } else if (msg.op === "call") {
      await boot();
      const [modName, fnName] = msg.target.split(":");
      py.globals.set("_jloa_target_mod", modName);
      py.globals.set("_jloa_target_fn", fnName);
      py.globals.set("_jloa_args_json", msg.argsJson);
      result = JSON.parse(py.runPython(CALL_SNIPPET));
    } else if (msg.op === "runJson") {
      await boot();
      py.globals.set("_jloa_args_json", msg.argsJson);
      result = JSON.parse(py.runPython(msg.code));
    } else if (msg.op === "callFrame") {
      await bootVision();
      const [modName, fnName] = msg.target.split(":");
      py.globals.set("_jloa_target_mod", modName);
      py.globals.set("_jloa_target_fn", fnName);
      py.globals.set("_jloa_args_json", msg.argsJson);
      py.globals.set("_jloa_frame", new Uint8Array(msg.buffer));
      py.globals.set("_jloa_frame_w", msg.width);
      py.globals.set("_jloa_frame_h", msg.height);
      result = JSON.parse(py.runPython(FRAME_SNIPPET));
    } else {
      throw new Error("알 수 없는 op: " + msg.op);
    }
    self.postMessage({ id: msg.id, ok: true, result: result });
  } catch (err) {
    self.postMessage({
      id: msg.id, ok: false,
      error: err && err.message ? err.message : String(err),
    });
  }
};
