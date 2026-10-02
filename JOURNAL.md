# Journal du projet Fanorona

Historique des étapes apportées au jeu, au moteur, au réseau et à l'interface, avec le résultat **mesuré** de
chacune (pas seulement « ce qui a été codé »). Le plus récent en haut. Une entrée par étape significative ; le
détail est dans le commit indiqué (`git show <commit>`).

Conventions : force mesurée par SPRT avec `tools/match.py` (bornes indiquées), vitesse par `bench` (nœuds/s,
fanorona-dev, i7-6700T, 1 thread), justesse par `make test`, `perft` et `tools/nnue/verify.py`.

---

## 2026-10-02

### NNUE — étiquettes à profondeur 9 et calibration de l'échelle : non retenus · `d1b98bd`
- Données `gensfen_gen5` : 13M positions à profondeur 9 (net_v3 professeur), 3 machines, ~2 h 30.
- Diagnostic : les scores de recherche sont trop confiants par rapport aux résultats des parties. À +500 cp,
  l'échelle 400 prédit 80 % des points ; les parties en donnent 68 % (gen4) et 59 % (gen5). Échelle optimale
  K ≈ 725 pour gen4, ≈ 1050 pour gen5 (avec elle, les deux jeux sont aussi cohérents : MSE 0,0374 / 0,0372).
- Nouvelles options : `train.py --score-scale K` (seule la perte change, la sortie reste lue en cp par le moteur)
  et `--wdl-weight W` (poids du résultat de partie dans la cible, 0,5 par défaut comme avant).
- Contre net_v3 à profondeur fixe 7, 2000 parties chacun (±0,9 %) :
  gen5 échelle 400 -> 48,2 % ; gen5 75 % résultat -> 50,1 % ; gen5 K=1050 -> 47,8 % ; gen4 K=725 -> 50,5 %.
- Décision : net_v3 reste le réseau par défaut ; pas de SPRT à 100 ms (aucun candidat au-dessus de 50 % à
  profondeur égale). Les deux derniers cycles montrent que le réseau (2×45 entrées, une couche cachée) est au
  plafond : le prochain gain viendra d'entrées plus riches ou d'une seconde couche, ou de la recherche.

### NNUE — 4ᵉ cycle de renforcement et réseau 512 : non retenus · `596df65`
- Données `gensfen_gen4` : 12M positions, `net_v3` comme professeur (profondeur 6), 13 processus sur les
  3 machines, ~18 min (contre 1 h 40 pour les 7,7M de gen3, réseau 4× plus lent à l'époque).
- Entraînement sur gen3 + gen4 (19,7M positions), 15 epochs : 256 neurones en 17 min (val_loss 0,01259),
  512 neurones en 30 min (val_loss 0,01205). Les deux passent `verify.py` (1000/1000 à ±1 cp).
- Vitesse `bench 8` : net_v3 1,25M nœuds/s, net_v4 256 1,28M, net_v4 512 0,89M (−30 %).
- Contre net_v3, 100 ms/coup, bornes 0/+10 : 256 -> W1408 D1376 L1384 (50,3 %, 4168 parties), 512 -> W797
  D759 L783 (50,4 %, 2339 parties). Aucun gain mesurable, arrêtés sans verdict.
- À profondeur fixe 7 (nouvelle option `match.py --depth`, 2000 parties) : 256 -> 51,8 %, 512 -> 52,1 %. Le
  léger gain d'évaluation vient surtout des nouvelles données, pas de la taille, et disparaît à temps égal.
- Décision : net_v3 reste le réseau par défaut. Leçon : le même professeur à la même profondeur n'apprend plus
  rien de neuf à l'élève ; il faut un meilleur signal (étiquettes plus profondes) plutôt que plus de neurones.
- Outils : `tools/nnue/train.py --hidden N`, `tools/match.py --depth N`.

## 2026-10-01

### Elo — correction de l'ajustement et recalibration · `af6ef17`
- **Bug** : l'ajustement Bradley-Terry de `tools/elo/calibrate.py` (montée de gradient à pas fixe) ne convergeait
  pas : log-vraisemblance −502 contre −380 pour l'algorithme MM de Hunter sur les mêmes parties, écarts du haut de
  l'échelle gonflés de plusieurs centaines d'Elo. Les « non-transitivités » invoquées lors des deux calibrations
  précédentes étaient pour l'essentiel cet artefact. Remplacé par MM (convergence garantie, critère < 0,01 Elo).
- Recalibration complète après LMP + futilité (744 parties) : niveaux 1 à 6 = 800, 1356, 1841, 1993, 2132, 2383
  (strictement croissants). Pour mémoire, les parties d'avant LMP, réajustées par MM : 800, 1393, 1822, 1958, 2135, 2368.

### Moteur — recherche : LMP + futilité des coups calmes · `c0d27cf`
- Positions sans capture (que des paika), profondeur <= 3, hors ligne principale : les derniers coups ne sont pas
  examinés (LMP) et les coups calmes sans espoir de remonter alpha sont sautés (futilité).
- SPRT (net_v3, 100 ms, bornes 0/+5, 3 machines) : H1 en 5362 parties, W1883 D1772 L1707 (51,6 %, ≈ +11 Elo).
- Séparément, chacun donnait ~51 % sans pouvoir être tranché en 3000 parties (bornes 0/+15).
- Essais rejetés de la même série (bornes 0/+15) : IIR 50,0 % (2350 parties), historique des captures 49,4 %
  (1397), capture unique forcée en quiescence 49,6 % (1537).
- Nouvelles signatures `bench 8` : HCE 572 736 nœuds, NNUE 933 003 nœuds ; perft inchangé.

### Outils — matchs répartis sur 3 machines · `25a382d`
- `tools/match.py --hosts` : moteurs lancés par SSH sur c1 (VMID 3190) et c3 (VMID 3191) en plus de fanorona-dev,
  11 parties simultanées au lieu de 5. 12 parties sur c1 + c3 en 17,5 s, fanorona-dev quasiment inactif.

### Outils — matchs en parallèle · `7ad28b3`
- `tools/match.py --concurrency N` : N paires de moteurs simultanées, SPRT mis à jour à chaque partie.
  12 parties : 47 s -> 22 s (N = 4). Exemple : NNUE (net_v3, accéléré) contre HCE, 30 ms/coup, bornes −30/+30 :
  H1 en 55 parties et 22 s (contre 90 parties lors du premier test NNUE, réseau 4× plus lent à l'époque).

### Règles — validation par une implémentation indépendante · `4e0c9ab`
- `tools/rules/reference.py` (générateur naïf, d'après les règles) contre le moteur : 60 000 positions
  (Tsivy et Dimy, arrêt libre et continuation obligatoire), ~454 000 coups notés identiques ; perft 1..5
  identiques (Tsivy 431 830, Dimy 34 608). Aucune divergence.
- Retour au point de départ pendant une chaîne : interdit (confirmé par l'exemple officiel de l'ICGA).

### Moteur — accélération NNUE ×4 · `278f080`
- 420 000 → ~1 700 000 nœuds/s au `bench 8` NNUE, **même signature** (1 121 922 nœuds : recherche identique).
- Cause réelle : absence de vectorisation (poids W1 lus par sauts de 90 flottants ; couche de sortie en
  réduction flottante non vectorisée). Corrigé par transposition de W1 au chargement et sortie AVX2/FMA.
- Hypothèse démentie : la taille du diff de l'accumulateur n'était pas le goulot (accumulateur par ply mesuré :
  12,2 → 10,1 colonnes/évaluation, aucun gain) — abandonné.
- SPRT contre l'ancien binaire (net_v3, 100 ms, bornes 0/+30) : H1 en 229 parties, W91 D79 L59 (57 %, ≈ +49 Elo).

### Interface — Elo recalibré · `3b2b524`
- Niveaux 1 à 6 : 800, 1366, 1861, 2039, 2124, 2643 (avant : …, 2787). Résultats non transitifs entre réglages :
  écarts du haut de l'échelle approximatifs.

### Maintenance · `20db52a`
- `logs/` ignoré ; nœuds de calcul c1/c3 mis à jour (même perft/bench que fanorona-dev).

### Interface — plateau plein écran en paysage · `1da451b`
- Téléphone en paysage : plateau 466×263 → 644×364 px (écran 844×390).

## 2026-09-30

### Interface — Elo du moteur et des joueurs · `fe500bf`
- Échelle de 13 réglages de force calibrée par tournoi (744 parties, Bradley-Terry, ancre Débutant = 800) ;
  curseur de force par Elo ; Elo de chaque profil mis à jour après chaque partie classée.

### Interface — application installable (PWA) · `d7b4114`
- Manifeste, service worker (coquille en cache), icônes ; vérifié installable en HTTPS.

### Moteur — MultiPV · `94e9162` · Interface — analyse en continu · `592e432`
- Option UCI MultiPV (1 à 8) ; avec MultiPV = 1, recherche et signature `bench` inchangées.
- Analyse en continu dans l'interface (sessions serveur, 1 à 3 lignes, flèches, barre d'évaluation).

### Interface — profils, refonte de la navigation · `ca5b414`
- Profils côté serveur (parties, classement puzzles, tutoriel) ; page d'accueil, vraies pages, barre d'onglets
  mobile ; la partie en cours est mise de côté pendant puzzles et tutoriel.

### Interface — historique des parties · `10450a9`
- SQLite côté serveur, bilan contre l'ordinateur, réouverture et analyse des parties.

### Interface — traduction malgache · `510f350`
- Tous les textes dans `tools/gui/i18n.json` (fr/mg), leçons traduites. Traduction à faire relire.

### Interface — export, partage, import (FGN) · `a3bff36`
- Format FGN (calqué sur PGN, spécifié dans le README), lien de partage sans stockage serveur.

### Puzzles tactiques · `378ea4e`
- 947 puzzles générés depuis l'auto-jeu (faute aléatoire puis punition unique), second avis NNUE (97 % d'accord).

### Interface — apprendre de ses erreurs · `ebba58c` · tutoriel interactif · `900fe73`
- Rejouer ses erreurs après analyse ; 6 chapitres / 13 exercices vérifiés par le moteur (`verify_lessons.py`).

### Moteur — variantes et vela · `d846a84` · Interface · `a89a99b`
- Fanoron-Dimy (5×5), Fanoron-Telo (3×3, résolu : les Blancs gagnent en 9 demi-coups), partie vela.
- Géométrie paramétrable sans changement du 9×5 (perft 5 = 431 830, `bench` inchangé).

### Interface — charte graphique · `6f6618d` … `708ea73`
- Thèmes Papier / Ardoise / Veille (nuit), plateaux bois et granite, marques de déplacement par plateau.

## 2026-09-29

### Interface web · `a63c3fb` · niveaux · `1901c8e` · refonte façon lichess · `a373e31`
- Serveur sans dépendance pilotant le moteur par UCI ; 6 niveaux (tirage pondéré, profondeur plafonnée, pleine
  force) ; pendules, analyse d'après-partie (?! ? ??), glisser-déposer.

## 2026-09-27

### Réseau NNUE — deux cycles de renforcement · `ffb2e77` · `bbfc1b8`
- net_v2 bat net_v1 (SPRT −30/+30 : H1 en 189 parties, 53,4 %) ; net_v3 bat net_v2 (343 parties, 51,75 %) :
  rendements décroissants mesurés. Génération de données répartie sur 3 nœuds PVE (7,7 M positions en 1 h 40).

### Réseau NNUE — inférence C++ et premier réseau · `af0e0ed` · `0aa5f37` · `c6a39ad`
- Inférence en C++, accumulateur incrémental par diff XOR ; net_v1 bat l'évaluation manuelle
  (SPRT −30/+30 : H1 en 90 parties, 56,1 %).

### Outils — SPRT et socle NNUE · `bd4d07a` · `9209cc2` · `59b6aaf` · `8943723`
- `tools/match.py` (SPRT), `gensfen`, entraînement PyTorch (correction OOM du DataLoader), `verify.py` (±1 cp).

### Moteur initial · `9edeb97` · `b5f12c6`
- Moteur C++17 inspiré de Stockfish : bitboards, alpha-bêta PVS, TT, quiescence, élagages ; ~2,5 M nœuds/s.
- perft depuis le départ : 5, 39, 724, 18 026, 431 830, 9 204 447 (seule la profondeur 1 vérifiée à la main).
