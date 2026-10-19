// 화면 테마 — 기본은 밝은 화면. OS 다크 설정은 따르지 않는다.
// ☀/☾ 버튼([data-theme-toggle])으로 바꾸고 이 브라우저에만 기억한다(localStorage — 막혀 있으면 기억만 안 된다).
// <head> 에서 동기로 읽힌다 → 본문을 그리기 전에 <html data-theme="dark"> 가 붙어 깜빡임이 없다.
// 시작 화면·도움말·대시보드가 같이 쓴다. 외부 출처 요청 없음(self 파일 하나 — 나중 script-src 'self' 통과).
(function () {
  "use strict";
  var KEY = "crm-theme";
  var root = document.documentElement;

  function read() {
    try { return window.localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function save(v) {
    try { window.localStorage.setItem(KEY, v); } catch (e) { /* 사생활 보호 모드 등 — 이번 화면에만 적용 */ }
  }
  function apply(theme) {
    if (theme === "dark") root.setAttribute("data-theme", "dark");
    else root.removeAttribute("data-theme");
  }
  function isDark() {
    return root.getAttribute("data-theme") === "dark";
  }
  // 버튼: 스크립트가 돌 때만 보인다(hidden 을 뗀다). 누름 상태 = 어두운 화면 켜짐.
  function sync() {
    var dark = isDark();
    var list = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < list.length; i++) {
      var b = list[i];
      b.hidden = false;
      b.textContent = dark ? "☀" : "☾";   // ☀ : ☾
      b.setAttribute("aria-pressed", dark ? "true" : "false");
      b.setAttribute("aria-label", "어두운 화면");
      b.title = dark ? "밝은 화면으로 바꾸기" : "어두운 화면으로 바꾸기";
    }
  }

  apply(read() === "dark" ? "dark" : "light");

  document.addEventListener("click", function (e) {
    var b = e.target && e.target.closest ? e.target.closest("[data-theme-toggle]") : null;
    if (!b) return;
    var next = isDark() ? "light" : "dark";
    apply(next);
    save(next);
    sync();
  });
  window.addEventListener("storage", function (e) {   // 다른 탭에서 바꾸면 따라간다
    if (e.key === KEY) { apply(e.newValue === "dark" ? "dark" : "light"); sync(); }
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", sync);
  else sync();
})();
