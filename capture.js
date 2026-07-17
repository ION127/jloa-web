/* JLOA 화면 공유 캡처 — getDisplayMedia 로 로스트아크 창을 받아
 * 일정 간격으로 캔버스 프레임(ImageData)을 콜백에 전달한다.
 * (데스크탑 앱 WGC 캡처의 웹 등가물 — 판독은 py-runtime 의 callFrame 이 담당)
 *
 * 사용:
 *   await JloaCapture.start(async (imageData) => { ... }, { intervalMs: 1200, onStop });
 *   JloaCapture.stop();      — 공유 종료 (사용자가 브라우저에서 끊어도 onStop 호출)
 */
const JloaCapture = (() => {
  let stream = null;
  let video = null;
  let timer = null;
  let busy = false;
  let stopCallback = null;
  const canvas = document.createElement("canvas");

  async function start(onFrame, opts) {
    stop();
    const intervalMs = (opts && opts.intervalMs) || 1200;
    stopCallback = (opts && opts.onStop) || null;

    // width/height ideal 을 크게 요청 — 브라우저가 트랙을 저해상도로 캡하면
    // 글리프가 뭉개져 판독이 실패한다 (원본 해상도 유지가 목적)
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 5, width: { ideal: 3840 }, height: { ideal: 2160 } },
      audio: false,
    });
    video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    await video.play();
    stream.getVideoTracks()[0].addEventListener("ended", () => stop());

    const settings = stream.getVideoTracks()[0].getSettings();

    timer = setInterval(async () => {
      if (busy || !video || video.readyState < 2 || !video.videoWidth) return;
      busy = true;
      try {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(video, 0, 0);
        await onFrame(ctx.getImageData(0, 0, canvas.width, canvas.height));
      } catch (err) {
        console.error("캡처 프레임 처리 실패", err);
      } finally {
        busy = false;
      }
    }, intervalMs);
    return settings;   // { width, height, … } — 실제 공유 해상도 (진단용)
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    video = null;
    busy = false;
    if (stopCallback) { const cb = stopCallback; stopCallback = null; cb(); }
  }

  return { start, stop, active: () => Boolean(stream) };
})();
