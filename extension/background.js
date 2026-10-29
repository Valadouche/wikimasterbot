// Logique du bot : un « tick » toutes les 30 s pilote l'onglet /pulls via content.js.

const PULLS_URL = "https://www.wiki-masters.com/pulls";
const INTERVAL_MS = 180_000; // un paquet toutes les 3 minutes
const TAB_REOPEN_MS = 300_000; // au plus un onglet rouvert toutes les 5 minutes
const MAX_LOGS = 200;

const DEFAULTS = { enabled: false, lastOpen: 0, total: 0, captcha: false, captchaNotified: false, lastTabOpen: 0, logs: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let busy = false; // empêche deux ticks simultanés (anti-doublon)

const getState = () => chrome.storage.local.get(DEFAULTS);
const setState = (patch) => chrome.storage.local.set(patch);

async function log(line, tabId) {
  const stamp = new Date().toLocaleTimeString("fr-FR");
  const { logs } = await getState();
  logs.push(`${stamp} ${line}`);
  await setState({ logs: logs.slice(-MAX_LOGS) });
  console.log(`[WM-bot] ${line}`);
  if (tabId) chrome.tabs.sendMessage(tabId, { type: "log", line }).catch(() => {});
}

function notify(message) {
  chrome.notifications.create({ type: "basic", iconUrl: "icon.png", title: "WikiMasters Auto-Packs", message, priority: 2 });
}

const send = (tabId, msg) => chrome.tabs.sendMessage(tabId, msg);

async function findPullsTab() {
  const tabs = await chrome.tabs.query({ url: "https://www.wiki-masters.com/*" });
  return tabs.find((t) => t.url && new URL(t.url).pathname.startsWith("/pulls")) || null;
}

// Fait défiler les cartes révélées et clique sur « Continuer ». Retourne les cartes vues.
async function revealCards(tabId) {
  // Attend la première carte. La vérification Cloudflare peut s'afficher puis se valider
  // seule en quelques secondes : on continue d'attendre pendant qu'elle est affichée.
  for (let i = 0; i < 40; i++) {
    const s = await send(tabId, { type: "status" });
    if (s.card) break;
    if (i === 39) return [];
    await sleep(500);
  }
  const cards = [];
  for (let i = 0; i < 15; i++) {
    await sleep(800); // laisse l'animation de la carte se terminer
    const s = await send(tabId, { type: "status" });
    if (!s.card) break;
    cards.push(s.card.rarity ? `[${s.card.rarity}] ${s.card.name}` : s.card.name);
    if (s.finish) {
      await send(tabId, { type: "click", target: "finish" });
      break;
    }
    if (!(await send(tabId, { type: "click", target: "next" }))) {
      await log("Flèche « carte suivante » introuvable, révélation interrompue.", tabId);
      break;
    }
  }
  return cards;
}

async function tick() {
  if (busy) return;
  busy = true;
  try {
    const state = await getState();
    if (!state.enabled) return;

    const tab = await findPullsTab();
    if (!tab) {
      if (Date.now() - state.lastTabOpen > TAB_REOPEN_MS) {
        await setState({ lastTabOpen: Date.now() });
        await log("Aucun onglet /pulls ouvert : ouverture en arrière-plan.");
        await chrome.tabs.create({ url: PULLS_URL, active: false });
      }
      return;
    }

    let s;
    try {
      s = await send(tab.id, { type: "status" });
    } catch {
      return; // page en cours de chargement : script pas encore injecté
    }

    if (s.captcha) {
      if (!state.captcha) {
        await setState({ captcha: true, captchaNotified: false });
        await log("Vérification Cloudflare affichée : le bot attend qu'elle soit validée.", tab.id);
      } else if (!state.captchaNotified) {
        // Toujours là au tick suivant (30 s) : elle ne se validera pas seule, on prévient
        await setState({ captchaNotified: true });
        notify("Vérification Cloudflare : cochez la case dans l'onglet WikiMasters.");
      }
      return;
    }
    if (state.captcha) {
      await setState({ captcha: false });
      await log("Vérification validée, reprise.", tab.id);
    }
    if (s.login) {
      await log("Compte déconnecté : reconnectez-vous, le bot est en pause.", tab.id);
      return;
    }

    if (s.inReveal) {
      // ex. ouverture qui a repris après validation d'un CAPTCHA
      const cards = await revealCards(tab.id);
      if (cards.length) {
        const total = state.total + 1;
        await setState({ total });
        await log(`Pack #${total} ouvert (révélation reprise) : ${cards.join(" | ")}`, tab.id);
      }
      return;
    }

    if (Date.now() - state.lastOpen < INTERVAL_MS) return;

    if (s.stock && s.stock.available === 0) {
      if (!s.stock.next) {
        await log("Stock à 0 sans minuteur : rechargement de la page.", tab.id);
        await send(tab.id, { type: "reload" }).catch(() => {});
      }
      return; // le prochain tick revérifie
    }
    if (!s.packButton) return;

    const n = state.total + 1;
    const stockInfo = s.stock ? ` (stock ${s.stock.available}/${s.stock.max})` : "";
    // Horodaté avant le clic : jamais deux ouvertures dans le même intervalle
    await setState({ lastOpen: Date.now() });
    await log(`Ouverture du pack #${n}${stockInfo}...`, tab.id);
    await send(tab.id, { type: "click", target: "pack" });

    const cards = await revealCards(tab.id);
    if (cards.length) {
      await setState({ total: n });
      await log(`Pack #${n} ouvert : ${cards.join(" | ")}`, tab.id);
    } else {
      const after = await send(tab.id, { type: "status" }).catch(() => null);
      await log(after?.captcha ? `Pack #${n} : ouverture interceptée par un CAPTCHA.` : `Pack #${n} : aucune carte affichée.`, tab.id);
    }
  } catch (e) {
    await log(`Erreur : ${e.message || e}`);
  } finally {
    busy = false;
  }
}

function ensureAlarm() {
  chrome.alarms.get("tick", (a) => a || chrome.alarms.create("tick", { periodInMinutes: 0.5 }));
}
chrome.runtime.onInstalled.addListener(ensureAlarm);
chrome.runtime.onStartup.addListener(ensureAlarm);
chrome.alarms.onAlarm.addListener((a) => a.name === "tick" && tick());

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "toggle") {
    setState({ enabled: msg.enabled }).then(async () => {
      await log(msg.enabled ? "Bot démarré." : "Bot arrêté.");
      ensureAlarm();
      if (msg.enabled) tick();
      reply(true);
    });
    return true;
  }
});
