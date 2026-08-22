// 主題切換（淺色 / 深色）。預設淺色，選擇會存在 localStorage，下次造訪沿用。
(function () {
  var STORAGE_KEY = "govbank-theme";
  var btn = document.getElementById("theme-toggle");
  if (!btn) return;

  function apply(theme) {
    if (theme === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
      btn.textContent = "☀️ 淺色模式";
      btn.setAttribute("aria-pressed", "true");
    } else {
      document.documentElement.removeAttribute("data-theme");
      btn.textContent = "🌙 深色模式";
      btn.setAttribute("aria-pressed", "false");
    }
  }

  var current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  apply(current);

  btn.addEventListener("click", function () {
    current = current === "dark" ? "light" : "dark";
    try { localStorage.setItem(STORAGE_KEY, current); } catch (e) { /* 忽略無法寫入的情況 */ }
    apply(current);
    // 讓頁面上的圖表（Chart.js 顏色是用 JS 設定，不會自動跟著 CSS 變數變）重新繪製。
    window.dispatchEvent(new CustomEvent("govbank:themechange"));
  });
})();
