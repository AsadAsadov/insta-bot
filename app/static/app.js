(() => {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("theme") || "light";
  root.dataset.bsTheme = savedTheme;

  const themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const next = root.dataset.bsTheme === "dark" ? "light" : "dark";
      root.dataset.bsTheme = next;
      localStorage.setItem("theme", next);
    });
  }

  const searchInput = document.getElementById("thread-search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      const term = (e.target.value || "").toLowerCase();
      document.querySelectorAll(".thread-item").forEach((el) => {
        const id = (el.dataset.threadId || "").toLowerCase();
        el.style.display = id.includes(term) ? "" : "none";
      });
    });
  }

  const quickReply = document.getElementById("quick-reply");
  const replyText = document.getElementById("reply-text");
  if (quickReply && replyText) {
    quickReply.addEventListener("change", () => {
      if (quickReply.value) replyText.value = quickReply.value;
    });
  }
})();
