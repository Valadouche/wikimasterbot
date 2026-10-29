# WikiMasters bot

## Extension Chrome (recommandé)

Ouvre un paquet toutes les 3 minutes **dans votre Chrome habituel**, avec votre session. Les vérifications Cloudflare y passent normalement : souvent seules, sinon une notification vous demande de cocher la case, et le bot reprend ensuite.

Installation (une fois) :
1. Ouvrez `chrome://extensions` et activez le **Mode développeur** (en haut à droite).
2. Cliquez sur **Charger l'extension non empaquetée** et choisissez le dossier `extension/` de ce dépôt.
3. Épinglez l'icône « WikiMasters Auto-Packs », cliquez dessus puis sur **Démarrer**.

Laissez un onglet `wiki-masters.com/pulls` ouvert (l'extension en ouvre un en arrière-plan sinon). Le popup affiche l'état, le nombre de paquets ouverts, la prochaine ouverture et les logs. Après une modification du code, cliquez sur ↻ dans `chrome://extensions`.

Comme pour la version Python : stock vérifié avant chaque clic, un seul clic par intervalle, cartes révélées puis « Continuer », boutons payants jamais cliqués, pause sur CAPTCHA ou déconnexion.

## Version Python / Playwright (ancienne)

Pilote un Chrome dédié via CDP. Cloudflare détecte ce mode et peut refuser la vérification : préférez l'extension.

## Installation (une fois)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Aucun navigateur Playwright n'est téléchargé : le bot pilote votre Google Chrome.

## Lancer Chrome avec CDP

```bash
./start_chrome.sh
```

Chrome ≥ 136 refuse le débogage distant sur le profil par défaut. Le script ouvre donc Chrome sur un profil dédié (`~/.wikimasters-chrome`). **La première fois, connectez-vous à WikiMasters dans cette fenêtre** ; la session est ensuite conservée d'un lancement à l'autre. Laissez l'onglet WikiMasters ouvert.

## Vérifier la détection (recommandé la première fois)

```bash
.venv/bin/python bot.py --inspect    # liste les boutons et indique celui qui sera cliqué
.venv/bin/python bot.py --dry-run    # tourne normalement mais sans cliquer
```

Si le bouton retenu n'est pas le bon, renseignez `pack_button_selector` dans `config.json` (ex. `"#open-pack"` ou `"button[data-testid=open-pack]"`) à partir de la sortie de `--inspect`.

## Démarrer / arrêter

```bash
./start.sh                 # en arrière-plan
tail -f logs/bot.log       # suivre les logs
./stop.sh                  # arrêt propre
```

Ou au premier plan : `.venv/bin/python bot.py` (Ctrl+C pour arrêter, `-v` pour des logs détaillés).

## Fonctionnement

- Trouve l'onglet dont l'URL contient `tab_url_contains` (sinon l'ouvre si `start_url` est défini).
- Toutes les `interval_seconds` (180), vérifie qu'un bouton de pack visible et actif existe, clique une seule fois puis attend la confirmation. Si aucun pack n'est disponible, réessaie toutes les `retry_seconds`.
- L'heure de la dernière ouverture est enregistrée dans `state.json` : pas de double ouverture, même après un redémarrage.
- CAPTCHA / Cloudflare / « vérifiez que vous êtes humain » : toutes les actions sont suspendues, une notification macOS est envoyée et le bot reprend une fois le défi résolu manuellement. Même chose si l'onglet passe sur une page de connexion.
- Chrome fermé ou planté, onglet perdu, erreur réseau : reconnexion automatique avec un délai croissant (5 s → 2 min). Le bot ne ferme jamais votre navigateur.
- Si le bouton reste introuvable pendant 2 intervalles, la page est rechargée.

## Configuration (`config.json`)

| Clé | Rôle |
|---|---|
| `cdp_url` | Adresse CDP de Chrome (`http://127.0.0.1:9222`) |
| `tab_url_contains` | Texte identifiant l'onglet WikiMasters dans l'URL |
| `start_url` | URL à ouvrir si l'onglet est introuvable (vide = attendre) |
| `interval_seconds` / `retry_seconds` | Intervalle entre packs / entre vérifications de disponibilité |
| `pack_button_selector` | Sélecteur CSS exact du bouton (prioritaire sur la regex) |
| `pack_button_text_regex` | Regex sur le texte du bouton si aucun sélecteur n'est défini |
| `result_selector` | Élément qui affiche le résultat ; son texte est écrit dans les logs |
| `dismiss_selector` | Bouton qui ferme la fenêtre de résultat après l'ouverture |
| `notify` | Notifications macOS (CAPTCHA, déconnexion) |
