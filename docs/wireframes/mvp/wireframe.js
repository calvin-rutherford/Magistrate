(function () {
  const routes = [...document.querySelectorAll(".screen")].map((node) => node.id);
  const drawer = document.getElementById("drawer");
  const drawerButton = document.getElementById("drawer-button");
  const toast = document.getElementById("toast");
  const shell = document.querySelector(".app-shell");
  const root = document.documentElement;

  function isMobile() {
    return window.matchMedia("(max-width: 820px)").matches;
  }

  function setDrawer(open) {
    drawer.classList.toggle("open", open);
    drawer.classList.toggle("closed", !open && !isMobile());
    drawerButton.setAttribute("aria-expanded", String(open));
  }

  function routeTo(id) {
    const route = routes.includes(id) ? id : "chat";
    document.querySelectorAll(".screen").forEach((screen) => {
      screen.classList.toggle("active", screen.id === route);
    });
    document.querySelectorAll("[data-route]").forEach((link) => {
      link.classList.toggle("active", link.dataset.route === route);
    });
    if (isMobile()) setDrawer(false);
    document.getElementById(route).querySelector("h1")?.focus?.();
  }

  function currentRoute() {
    return (location.hash || "#chat").slice(1);
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("show"), 2600);
  }

  window.addEventListener("hashchange", () => routeTo(currentRoute()));
  window.addEventListener("resize", () => setDrawer(!isMobile()));

  document.querySelectorAll("[data-route]").forEach((link) => {
    link.addEventListener("click", () => routeTo(link.dataset.route));
  });

  drawerButton.addEventListener("click", () => {
    const next = !drawer.classList.contains("open") && !drawer.classList.contains("closed");
    if (isMobile()) setDrawer(!drawer.classList.contains("open"));
    else setDrawer(next);
  });

  document.getElementById("fleet-toggle").addEventListener("click", (event) => {
    const list = document.getElementById("fleet-list");
    const expanded = event.currentTarget.getAttribute("aria-expanded") === "true";
    event.currentTarget.setAttribute("aria-expanded", String(!expanded));
    list.hidden = expanded;
  });

  document.querySelectorAll("[data-demo-toast]").forEach((button) => {
    button.addEventListener("click", () => showToast(button.dataset.demoToast));
  });

  document.querySelectorAll("[data-bg]").forEach((button) => {
    button.addEventListener("click", () => {
      shell.dataset.background = button.dataset.bg;
      showToast(`${button.textContent.trim()} background selected for review.`);
    });
  });

  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-mode]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      showToast(`${button.textContent.trim()} background mode selected.`);
    });
  });

  document.getElementById("theme-toggle").addEventListener("click", (event) => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    event.currentTarget.textContent = next === "dark" ? "Light mode" : "Dark mode";
  });

  const voiceStates = ["Listening", "Thinking", "Speaking", "Listening"];
  let voiceIndex = 0;
  document.getElementById("cycle-voice").addEventListener("click", () => {
    voiceIndex = (voiceIndex + 1) % voiceStates.length;
    document.getElementById("voice-state").textContent = voiceStates[voiceIndex];
    document.getElementById("live-transcript").textContent = voiceStates[voiceIndex] === "Thinking"
      ? "Firstmate is preparing the spoken response..."
      : voiceStates[voiceIndex] === "Speaking"
        ? "The staging deployment is reversible and checks are passing."
        : "\"Deploy it after the tests pass...\"";
  });

  const previews = {
    loading: ["Loading", "Skeleton rows and disabled composer. No fake chat transcript is inserted."],
    reconnecting: ["Reconnecting", "Sending is disabled until transport recovers; queued-message behavior is stated explicitly."],
    offline: ["Offline", "The app keeps readable history visible and labels unavailable actions."],
    permission: ["Permission denied", "Provider access is denied cleanly without suggesting the account is connected."],
    empty: ["Empty", "Honest empty state shown when no durable task, event, or conversation exists."],
    clear: ["Choose a state", "Each state disables or labels behavior instead of filling the product with fake data."],
  };

  document.querySelectorAll("[data-state]").forEach((button) => {
    button.addEventListener("click", () => {
      const [title, body] = previews[button.dataset.state];
      document.getElementById("state-preview").innerHTML = `<h2>${title}</h2><p>${body}</p>`;
      showToast(`${title} state selected.`);
    });
  });

  setDrawer(!isMobile());
  routeTo(currentRoute());
})();
