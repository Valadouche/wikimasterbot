# WikiMasters Auto-Packs

Extension Chrome qui ouvre un paquet WikiMasters toutes les 3 minutes **dans votre Chrome habituel**, avec votre session. Les vérifications Cloudflare y passent normalement : souvent seules, sinon une notification vous demande de cocher la case, et l'extension reprend ensuite.

## Installation (une fois)

1. Ouvrez `chrome://extensions` et activez le **Mode développeur** (en haut à droite).
2. Cliquez sur **Charger l'extension non empaquetée** et choisissez le dossier `extension/` de ce dépôt.
3. Épinglez l'icône « WikiMasters Auto-Packs », cliquez dessus puis sur **Démarrer**.

Après une modification du code, cliquez sur ↻ dans `chrome://extensions`.

## Utilisation

- Laissez un onglet `wiki-masters.com/pulls` ouvert (l'extension en ouvre un en arrière-plan sinon, au plus une fois toutes les 5 minutes).
- Le popup affiche l'état, le nombre de paquets ouverts, la prochaine ouverture et les logs ; le bouton Démarrer / Arrêter met le bot en marche ou en pause.
- Le Mac ne doit pas être en veille (capot ouvert ou branché sur secteur).

## Fonctionnement

- Un tick toutes les 30 s (`chrome.alarms`) : le service worker (`background.js`) lit l'état de la page via `content.js` et décide de l'action.
- Avant chaque ouverture : stock lu sur la page (« N / 10 paquets disponibles ») ; rien n'est cliqué à 0. Un stock à 0 sans minuteur (page périmée) déclenche un rechargement.
- Un seul clic par intervalle de 3 minutes, horodaté avant le clic : pas de doublon.
- Après l'ouverture : les 5 cartes défilent (rareté et nom dans les logs) puis « Continuer ». Une révélation laissée en cours est terminée au tick suivant.
- Vérification Cloudflare : l'extension attend ; si elle ne se valide pas seule en 30 s, une notification vous demande de cocher la case.
- Les boutons payants (« Rechargez 10 paquets — 1,99 $ »…) ne sont jamais cliqués.

## Fichiers

| Fichier | Rôle |
|---|---|
| `extension/manifest.json` | Déclaration de l'extension (Manifest V3) |
| `extension/background.js` | Logique du bot : minuterie, décisions, logs, notifications |
| `extension/content.js` | Lecture de la page et clics, sur demande du service worker |
| `extension/popup.html`, `popup.js` | Popup : état, compteur, logs, Démarrer / Arrêter |
