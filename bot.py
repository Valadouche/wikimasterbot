#!/usr/bin/env python3
"""Bot WikiMasters : ouvre un pack toutes les N secondes dans la session Chrome existante (via CDP)."""
import argparse
import json
import logging
import logging.handlers
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Error as PWError
from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
LOG_PATH = ROOT / "logs" / "bot.log"

DEFAULT_CONFIG = {
    "cdp_url": "http://127.0.0.1:9222",
    "tab_url_contains": "wiki-masters.com/pulls",
    "start_url": "https://www.wiki-masters.com/pulls",
    "interval_seconds": 180,
    "retry_seconds": 30,
    "pack_button_selector": "",
    "pack_button_text_regex": r"^\s*(ouvrir|open)( (un|le|a) (paquet|pack|booster))?\s*$",
    "stock_text_regex": r"(\d+)\s*/\s*(\d+)\s*paquets disponibles(?:\s*Prochain dans\s*([\d:]+))?",
    "card_title_selector": "main h3",
    "next_card_selector": "main button:not([aria-label]):has(svg)",
    "finish_button_text_regex": r"^\s*(continuer|terminer|fermer)\s*$",
    "reveal_button_text_regex": r"^\s*(encore \d+ cartes?|continuer)\s*$",
    "login_url_regex": r"login|signin|sign-in|connexion|/auth",
    "confirm_timeout_seconds": 15,
    "screenshot_dir": "",
    "notify": True,
}

CLICKABLE = "button, [role=button], a, input[type=button], input[type=submit]"

# Iframes des fournisseurs de CAPTCHA / challenge anti-bot
CAPTCHA_FRAME_RE = re.compile(
    r"recaptcha|hcaptcha|challenges\.cloudflare\.com|turnstile|arkoselabs|funcaptcha|geetest|captcha", re.I
)
# Textes typiques d'une page de vérification (pas le simple mot "captcha", trop présent dans les footers)
CAPTCHA_TEXT_RE = re.compile(
    r"verify (that )?you are (a )?human|v[ée]rifi(ez|er|ons) que vous [êe]tes (un )?humain|"
    r"i'?m not a robot|je ne suis pas un robot|checking your browser|v[ée]rification de votre navigateur|"
    r"unusual traffic|trafic inhabituel|press (and|&) hold|appuyez et maintenez|que tu es (bien )?humain|v[ée]rification rapide",
    re.I,
)
CAPTCHA_TITLE_RE = re.compile(r"just a moment|un instant|attention required|captcha", re.I)

ENABLED_JS = """e => !(e.disabled || e.getAttribute('aria-disabled') === 'true'
    || /(^|\\s|-)disabled(\\s|$)/i.test(e.getAttribute('class') || '')
    || getComputedStyle(e).pointerEvents === 'none')"""

# Rareté de la carte : badge court en majuscules (C, PC, R...) proche du titre
RARITY_JS = """h => { let e = h;
    for (let i = 0; i < 6 && e; i++) { e = e.parentElement;
        const m = e && [...e.querySelectorAll('*')].find(x => x.children.length === 0
            && /^[A-Z]{1,3}$/.test((x.textContent || '').trim()));
        if (m) return m.textContent.trim(); }
    return ''; }"""

# Boutons payants (« Rechargez 10 paquets — 1,99 $ », abonnement...) : jamais cliqués
PAID_RE = re.compile(r"[$€£]|recharg|achet|\bbuy\b|\bpay|abonn|subscri", re.I)

log = logging.getLogger("wikimasters")
STOP = False


def safe_click(el: Locator) -> None:
    text = " ".join(el.inner_text().split())
    if PAID_RE.search(text):
        raise RuntimeError(f"Clic refusé sur un bouton payant : {text!r}")
    el.click(timeout=10_000)


def _request_stop(signum, _frame):
    global STOP
    STOP = True
    log.info("Signal %s reçu, arrêt en cours...", signal.Signals(signum).name)


def sleep(seconds: float) -> None:
    """Sommeil interruptible par Ctrl+C / SIGTERM."""
    end = time.monotonic() + seconds
    while not STOP and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def setup_logging(verbose: bool) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logfile = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logfile.setFormatter(fmt)
    log.addHandler(console)
    log.addHandler(logfile)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def notify(cfg: dict, message: str) -> None:
    if not cfg["notify"] or sys.platform != "darwin":
        return
    script = f'display notification "{message}" with title "WikiMasters bot" sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


class State:
    """Horodatage de la dernière ouverture, persisté pour éviter un doublon après redémarrage."""

    def __init__(self):
        data = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
        self.last_open = float(data.get("last_open", 0))
        self.total = int(data.get("total", 0))

    def record_attempt(self) -> None:
        """Horodate le clic : bloque tout nouveau clic avant l'intervalle, même si l'ouverture échoue."""
        self.last_open = time.time()
        self.save()

    def record_open(self) -> None:
        self.total += 1
        self.save()

    def save(self) -> None:
        STATE_PATH.write_text(json.dumps({"last_open": self.last_open, "total": self.total}))


class Bot:
    def __init__(self, cfg: dict, dry_run: bool):
        self.cfg = cfg
        self.dry_run = dry_run
        self.state = State()
        self.browser = None
        self.own_page: Optional[Page] = None  # onglet ouvert par le bot, le cas échéant
        self.active_pages: list = []  # onglets déjà protégés contre la mise en veille
        self.last_tab_open = -1e9  # horodatage (monotonic) du dernier onglet ouvert par le bot
        self.not_found_since: Optional[float] = None

    # --- Connexion -------------------------------------------------------

    def connect(self, pw) -> None:
        log.info("Connexion à Chrome via CDP (%s)...", self.cfg["cdp_url"])
        self.ensure_window()
        self.browser = pw.chromium.connect_over_cdp(self.cfg["cdp_url"], timeout=15_000)
        log.info("Connecté à Chrome %s", self.browser.version)

    def ensure_window(self) -> None:
        """Chrome sans aucune fenêtre (macOS) refuse la connexion Playwright : on rouvre un onglet."""
        try:
            with urlopen(f"{self.cfg['cdp_url']}/json/list", timeout=5) as r:
                if any(t["type"] == "page" for t in json.load(r)):
                    return
        except OSError:
            return  # Chrome injoignable : l'erreur sera remontée par connect_over_cdp
        if not self.cfg["start_url"] or time.monotonic() - self.last_tab_open < 300:
            return
        self.last_tab_open = time.monotonic()
        log.warning("Chrome n'a plus aucune fenêtre, ouverture de %s", self.cfg["start_url"])
        urlopen(Request(f"{self.cfg['cdp_url']}/json/new?{self.cfg['start_url']}", method="PUT"), timeout=5).close()
        time.sleep(3)

    def get_page(self) -> Optional[Page]:
        """Retrouve l'onglet WikiMasters existant (ou l'ouvre si start_url est configuré)."""
        if self.browser is None or not self.browser.is_connected():
            raise PWError("Navigateur déconnecté")
        needle = self.cfg["tab_url_contains"].lower()
        host = urlparse(self.cfg["start_url"]).netloc.lower()
        pages = [p for c in self.browser.contexts for p in c.pages]
        for page in pages:
            if needle in page.url.lower():
                return page
        # Onglet du site redirigé vers la connexion : on le garde pour détecter la déconnexion
        for page in pages:
            if host and host in page.url.lower() and self.needs_login(page):
                return page
        # Onglet tombé sur une page d'erreur (réseau coupé, réveil de veille...) : on le recharge
        for page in pages:
            if page.url.startswith("chrome-error://") or (host and host in page.url.lower()):
                log.warning("Onglet sur %s, retour sur %s", page.url[:60], self.cfg["start_url"])
                page.goto(self.cfg["start_url"], wait_until="domcontentloaded")
                return page
        if self.own_page is not None and not self.own_page.is_closed():
            log.warning("L'onglet du bot a quitté la page des paquets, retour sur %s", self.cfg["start_url"])
            self.own_page.goto(self.cfg["start_url"], wait_until="domcontentloaded")
            return self.own_page
        if self.cfg["start_url"] and self.browser.contexts:
            # Onglet fermé à répétition (par l'utilisateur ou le site) : on ne boucle pas
            if time.monotonic() - self.last_tab_open < 300:
                return None
            self.last_tab_open = time.monotonic()
            log.warning("Aucun onglet WikiMasters trouvé, ouverture de %s", self.cfg["start_url"])
            self.own_page = self.browser.contexts[0].new_page()
            self.own_page.goto(self.cfg["start_url"], wait_until="domcontentloaded")
            return self.own_page
        return None

    def keep_active(self, page: Page) -> None:
        """Empêche Chrome de geler / ralentir l'onglet quand la fenêtre est réduite ou cachée."""
        if page in self.active_pages:
            return
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            cdp.send("Page.setWebLifecycleState", {"state": "active"})
            self.active_pages.append(page)
        except PWError as e:
            log.debug("Maintien actif de l'onglet impossible : %s", e)

    # --- Détections ------------------------------------------------------

    def captcha_present(self, page: Page) -> bool:
        try:
            if CAPTCHA_TITLE_RE.search(page.title()):
                return True
            for frame in page.frames:
                if frame is page.main_frame or not CAPTCHA_FRAME_RE.search(frame.url or ""):
                    continue
                if "size=invisible" in frame.url:  # badge reCAPTCHA v3, pas un défi
                    continue
                box = frame.frame_element().bounding_box()
                if box and box["width"] > 50 and box["height"] > 50:
                    return True
            body = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 5000)")
            return bool(CAPTCHA_TEXT_RE.search(body))
        except PWError as e:
            log.debug("Vérification CAPTCHA impossible : %s", e)
            return False

    def needs_login(self, page: Page) -> bool:
        return bool(re.search(self.cfg["login_url_regex"], page.url, re.I))

    def wait_manual(self, page: Page, reason: str, check) -> None:
        """Suspend toute action tant que check(page) est vrai (résolution manuelle)."""
        log.warning("%s détecté(e) : actions suspendues jusqu'à résolution manuelle dans Chrome.", reason)
        notify(self.cfg, f"{reason} : intervention requise")
        started = time.monotonic()
        while not STOP and check(page):
            sleep(5)
            if time.monotonic() - started > 600:
                notify(self.cfg, f"{reason} toujours en attente")
                started = time.monotonic()
        if not STOP:
            log.info("%s résolu(e), reprise dans 5 s.", reason)
            sleep(5)

    def find_button(self, page: Page, text_regex: str, selector: str = "") -> Optional[Locator]:
        """Premier élément cliquable visible et actif correspondant au sélecteur ou à la regex."""
        if selector:
            candidates = page.locator(selector)
        else:
            candidates = page.locator(CLICKABLE).filter(has_text=re.compile(text_regex, re.I))
        for i in range(min(candidates.count(), 20)):
            el = candidates.nth(i)
            if el.is_visible() and el.evaluate(ENABLED_JS) and not PAID_RE.search(el.inner_text()):
                return el
        return None

    def find_pack_button(self, page: Page) -> Optional[Locator]:
        return self.find_button(page, self.cfg["pack_button_text_regex"], self.cfg["pack_button_selector"])

    def read_stock(self, page: Page) -> Optional[tuple]:
        """(disponibles, maximum, prochain) d'après le compteur affiché, ou None s'il est absent."""
        text = page.evaluate("() => (document.querySelector('main') || document.body).innerText")
        m = re.search(self.cfg["stock_text_regex"], text, re.I)
        return (int(m.group(1)), int(m.group(2)), m.group(3) or "?") if m else None

    # --- Ouverture -------------------------------------------------------

    def open_pack(self, page: Page, button: Locator) -> None:
        label = " ".join(button.inner_text().split())[:60]
        n = self.state.total + 1
        if self.dry_run:
            log.info("[dry-run] Pack disponible, clic simulé sur %r", label)
            self.state.last_open = time.time()
            return
        log.info("Ouverture du pack #%d (bouton %r)...", n, label)
        safe_click(button)
        # Enregistré immédiatement : même non confirmé, on ne reclique pas avant le prochain intervalle
        self.state.record_attempt()
        cards = self.reveal_cards(page, shot_name=f"pack-{n:04d}")
        if cards:
            self.state.record_open()
            log.info("Pack #%d ouvert : %s", n, " | ".join(cards))
        elif self.captcha_present(page):
            log.warning("Pack #%d : ouverture interceptée par une vérification anti-robot.", n)
        else:
            log.warning("Pack #%d : clic effectué mais aucune carte affichée (ouverture non confirmée).", n)
        next_at = time.localtime(self.state.last_open + self.cfg["interval_seconds"])
        log.info("Prochaine ouverture vers %s.", time.strftime("%H:%M:%S", next_at))

    def reveal_cards(self, page: Page, shot_name: str = "") -> list:
        """Fait défiler les cartes révélées, clique sur « Continuer » et retourne les cartes obtenues."""
        title = page.locator(self.cfg["card_title_selector"]).first
        try:
            title.wait_for(state="visible", timeout=self.cfg["confirm_timeout_seconds"] * 1000)
        except PWError:
            return []
        cards = []
        for _ in range(15):
            sleep(0.8)  # laisse l'animation de la carte se terminer
            name = " ".join(title.inner_text().split())
            rarity = title.evaluate(RARITY_JS)
            cards.append(f"[{rarity}] {name}" if rarity else name)
            finish = self.find_button(page, self.cfg["finish_button_text_regex"])
            if finish is not None:
                self.screenshot(page, shot_name)
                safe_click(finish)
                break
            nxt = page.locator(self.cfg["next_card_selector"]).last
            # La flèche « suivante » est une icône sans texte : tout autre bouton est ignoré
            if not nxt.count() or nxt.inner_text().strip() or not nxt.evaluate(ENABLED_JS):
                log.warning("Bouton « carte suivante » introuvable, révélation interrompue.")
                break
            safe_click(nxt)
        return cards

    def screenshot(self, page: Page, name: str) -> None:
        if not self.cfg["screenshot_dir"] or not name:
            return
        folder = ROOT / self.cfg["screenshot_dir"]
        folder.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(folder / f"{name}.png"), timeout=10_000)
        except PWError as e:
            log.debug("Capture impossible : %s", e)

    def in_reveal(self, page: Page) -> bool:
        return self.find_button(page, self.cfg["reveal_button_text_regex"]) is not None

    # --- Boucle principale -----------------------------------------------

    def cycle(self) -> None:
        page = self.get_page()
        if page is None:
            log.warning("Onglet WikiMasters introuvable (URL contenant %r). Nouvel essai dans %ds.",
                        self.cfg["tab_url_contains"], self.cfg["retry_seconds"])
            sleep(self.cfg["retry_seconds"])
            return
        self.keep_active(page)
        if self.captcha_present(page):
            self.wait_manual(page, "CAPTCHA / vérification anti-robot", self.captcha_present)
            return
        if self.needs_login(page):
            self.wait_manual(page, "Déconnexion du compte", self.needs_login)
            return

        if self.in_reveal(page):  # révélation laissée en cours (redémarrage, action manuelle...)
            # ex. pack dont l'ouverture a repris après résolution d'un CAPTCHA
            n = self.state.total + 1
            cards = self.reveal_cards(page, shot_name=f"pack-{n:04d}")
            if cards:
                self.state.record_open()
                log.info("Pack #%d ouvert (révélation reprise) : %s", n, " | ".join(cards))
            return

        remaining = self.state.last_open + self.cfg["interval_seconds"] - time.time()
        if remaining > 0:
            log.debug("Prochaine ouverture dans %ds.", remaining)
            sleep(min(remaining, 15))  # réveil régulier pour surveiller CAPTCHA / onglet
            return

        stock = self.read_stock(page)
        if stock and stock[0] == 0 and stock[2] == "?":
            # Stock vide sans minuteur : données périmées (veille, coupure réseau...)
            log.info("Stock à 0 sans minuteur : rechargement de la page.")
            page.reload(wait_until="domcontentloaded")
            sleep(5)
            return
        if stock and stock[0] == 0:
            # Attend la fin du minuteur affiché par le site (+3 s de marge)
            m = re.fullmatch(r"(\d+):(\d{2})", stock[2])
            wait = int(m.group(1)) * 60 + int(m.group(2)) + 3 if m else self.cfg["retry_seconds"]
            log.info("Aucun paquet disponible (0/%d, prochain dans %s). Nouvel essai dans %ds.",
                     stock[1], stock[2], wait)
            sleep(wait)
            return
        button = self.find_pack_button(page)
        if button is None:
            now = time.monotonic()
            self.not_found_since = self.not_found_since or now
            log.info("Bouton d'ouverture introuvable ou inactif. Nouvel essai dans %ds.", self.cfg["retry_seconds"])
            if now - self.not_found_since > 2 * self.cfg["interval_seconds"]:
                log.warning("Bouton introuvable depuis longtemps : rechargement de la page.")
                page.reload(wait_until="domcontentloaded")
                self.not_found_since = now
            sleep(self.cfg["retry_seconds"])
            return
        self.not_found_since = None
        if stock:
            log.info("Stock : %d/%d paquets disponibles.", stock[0], stock[1])
        self.open_pack(page, button)

    def run(self) -> None:
        backoff = 5
        with sync_playwright() as pw:
            while not STOP:
                try:
                    if self.browser is None or not self.browser.is_connected():
                        self.connect(pw)
                    self.cycle()
                    backoff = 5
                except PWError as e:
                    first_line = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
                    log.error("Erreur Playwright : %s. Reconnexion dans %ds.", first_line, backoff)
                    self.browser = None  # pas de close() : cela fermerait la session de l'utilisateur
                    self.own_page = None
                    self.active_pages = []
                    sleep(backoff)
                    backoff = min(backoff * 2, 120)
                except Exception:
                    log.exception("Erreur inattendue. Reprise dans %ds.", backoff)
                    sleep(backoff)
                    backoff = min(backoff * 2, 120)
        log.info("Bot arrêté. Total de packs ouverts : %d.", self.state.total)


def inspect(cfg: dict) -> None:
    """Affiche les onglets et les éléments cliquables pour aider à configurer le sélecteur."""
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cfg["cdp_url"], timeout=15_000)
        bot = Bot(cfg, dry_run=True)
        bot.browser = browser
        print("Onglets ouverts :")
        for context in browser.contexts:
            for page in context.pages:
                print(f"  - {page.url}")
        page = bot.get_page()
        if page is None:
            print(f"\nAucun onglet contenant {cfg['tab_url_contains']!r}.")
            return
        print(f"\nOnglet WikiMasters : {page.url}")
        print(f"CAPTCHA détecté : {bot.captcha_present(page)} | page de connexion : {bot.needs_login(page)}")
        elements = page.evaluate(
            """sel => [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null).map(e => ({
                tag: e.tagName.toLowerCase(), text: (e.innerText || e.value || '').trim().replace(/\\s+/g, ' ').slice(0, 50),
                id: e.id, cls: (e.getAttribute('class') || '').slice(0, 60), testid: e.getAttribute('data-testid') || ''
            }))""",
            CLICKABLE,
        )
        print(f"\n{len(elements)} éléments cliquables visibles :")
        for e in elements:
            print(f"  <{e['tag']}> {e['text']!r} id={e['id']!r} class={e['cls']!r} data-testid={e['testid']!r}")
        button = bot.find_pack_button(page)
        print("\nBouton de pack retenu :", repr(button.inner_text().strip()) if button else "AUCUN")
        print("Stock (disponibles, max, prochain) :", bot.read_stock(page))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ouvre automatiquement les packs WikiMasters.")
    parser.add_argument("--inspect", action="store_true", help="analyse la page sans cliquer")
    parser.add_argument("--dry-run", action="store_true", help="détecte les packs sans cliquer")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs détaillés")
    args = parser.parse_args()

    cfg = load_config()
    if args.inspect:
        inspect(cfg)
        return
    setup_logging(args.verbose)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    log.info("Démarrage du bot (intervalle %ds%s).", cfg["interval_seconds"], ", dry-run" if args.dry_run else "")
    Bot(cfg, args.dry_run).run()


if __name__ == "__main__":
    main()
