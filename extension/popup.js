const $ = (id) => document.getElementById(id);
const DEFAULTS = { enabled: false, lastOpen: 0, total: 0, captcha: false, logs: [] };

async function render() {
  const s = await chrome.storage.local.get(DEFAULTS);
  $("status").textContent = !s.enabled ? "Arrêté" : s.captcha ? "CAPTCHA à valider" : "En marche";
  $("status").className = s.captcha && s.enabled ? "warn" : "";
  $("total").textContent = s.total;
  const next = s.lastOpen + 180_000;
  $("next").textContent = s.enabled ? (next > Date.now() ? new Date(next).toLocaleTimeString("fr-FR") : "dès que possible") : "-";
  $("toggle").textContent = s.enabled ? "Arrêter" : "Démarrer";
  $("toggle").className = s.enabled ? "on" : "off";
  $("logs").textContent = s.logs.slice(-50).reverse().join("\n");
}

$("toggle").addEventListener("click", async () => {
  const { enabled } = await chrome.storage.local.get(DEFAULTS);
  await chrome.runtime.sendMessage({ type: "toggle", enabled: !enabled });
  render();
});

chrome.storage.onChanged.addListener(render);
render();
