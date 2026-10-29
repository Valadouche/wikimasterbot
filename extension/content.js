// Actionneur sans état : lit la page et clique sur demande du service worker (background.js).
// Toute la temporisation est dans le service worker : les minuteurs des onglets en arrière-plan
// sont fortement ralentis par Chrome, ceux du service worker non.

const RE = {
  pack: /^\s*(ouvrir|open)( (un|le|a) (paquet|pack|booster))?\s*$/i,
  stock: /(\d+)\s*\/\s*(\d+)\s*paquets disponibles(?:\s*Prochain dans\s*([\d:]+))?/i,
  finish: /^\s*(continuer|terminer|fermer)\s*$/i,
  reveal: /^\s*(encore \d+ cartes?|continuer)\s*$/i,
  // Boutons payants (« Rechargez 10 paquets — 1,99 $ »...) : jamais cliqués
  paid: /[$€£]|recharg|achet|\bbuy\b|\bpay|abonn|subscri/i,
  captchaText: /v[ée]rification rapide|que tu es (bien )?humain|verify (that )?you are (a )?human|checking your browser|just a moment/i,
  captchaFrame: /challenges\.cloudflare\.com|turnstile|hcaptcha|recaptcha/i,
};

const text = (el) => (el.innerText || "").replace(/\s+/g, " ").trim();
const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
const enabled = (el) =>
  !(el.disabled || el.getAttribute("aria-disabled") === "true" || getComputedStyle(el).pointerEvents === "none");

function findButton(re) {
  return [...document.querySelectorAll("button, [role=button]")].find(
    (el) => visible(el) && enabled(el) && re.test(text(el)) && !RE.paid.test(text(el))
  );
}

// Flèche « carte suivante » : dernière icône sans texte ni aria-label de la zone principale
function nextArrow() {
  const arrows = [...document.querySelectorAll("main button:not([aria-label])")].filter(
    (b) => b.querySelector("svg") && !text(b) && visible(b)
  );
  const last = arrows[arrows.length - 1];
  return last && enabled(last) ? last : null;
}

function captchaPresent() {
  const frame = [...document.querySelectorAll("iframe")].some((f) => {
    const r = f.getBoundingClientRect();
    return RE.captchaFrame.test(f.src) && !/size=invisible/.test(f.src) && r.width > 50 && r.height > 50;
  });
  return frame || RE.captchaText.test(document.title) || RE.captchaText.test(document.body.innerText || "");
}

function readStock() {
  const m = ((document.querySelector("main") || document.body).innerText || "").match(RE.stock);
  return m ? { available: +m[1], max: +m[2], next: m[3] || null } : null;
}

// Rareté : badge court en majuscules (C, PC, R...) proche du titre de la carte
function rarityOf(h) {
  let e = h;
  for (let i = 0; i < 6 && e; i++) {
    e = e.parentElement;
    const m = e && [...e.querySelectorAll("*")].find((x) => x.children.length === 0 && /^[A-Z]{1,3}$/.test((x.textContent || "").trim()));
    if (m) return m.textContent.trim();
  }
  return "";
}

function status() {
  const title = document.querySelector("main h3");
  const card = title && visible(title) ? text(title) : null;
  return {
    url: location.href,
    captcha: captchaPresent(),
    login: /\/(login|signin|auth)/i.test(location.pathname),
    stock: readStock(),
    packButton: !!findButton(RE.pack),
    inReveal: !!findButton(RE.reveal),
    card: card && { name: card, rarity: rarityOf(title) },
    finish: !!findButton(RE.finish),
    next: !!nextArrow(),
  };
}

function click(target) {
  const el = { pack: () => findButton(RE.pack), finish: () => findButton(RE.finish), next: nextArrow }[target]?.();
  if (!el || RE.paid.test(text(el))) return false;
  el.click();
  return true;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "status") reply(status());
  else if (msg.type === "click") reply(click(msg.target));
  else if (msg.type === "reload") { reply(true); location.reload(); }
  else if (msg.type === "log") { console.log(`[WM-bot] ${msg.line}`); reply(true); }
});
