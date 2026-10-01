# CLAUDE.md — Fanorona-Engine

Moteur de Fanorona (plateau 9×5, jeu malgache) en C++17, construit sur les principes de Stockfish.
Voir `README.md` pour les règles, la notation et les commandes utilisateur.

## Commandes

```sh
make              # construit ./fanorona (-O3 -march=native)
make test         # tests : ./tests/run_tests (doit afficher "Tous les tests passent.")
make bench        # ./fanorona bench -> "Nodes searched" et NPS
./fanorona perft 5                       # doit donner 431830
./fanorona "position startpos" ...        # un argument = une commande, puis sortie
python3 tools/match.py ./fanorona ./fanorona-old --games 20 --movetime 100   # auto-jeu, parties fixes
python3 tools/match.py ./fanorona ./fanorona-old --sprt --elo0 0 --elo1 5 --movetime 100  # auto-jeu, arrêt SPRT
./fanorona gensfen count 1000000 depth 6 opening-plies 8 out data/gensfen.txt   # données NNUE (auto-jeu)
python3 tools/nnue/train.py data/gensfen.txt --epochs 20 --out checkpoints/net.pt   # entraînement NNUE (PyTorch, CPU)
python3 tools/nnue/verify.py checkpoints/net.nnue --samples data/gensfen.txt --n 300   # C++ == référence numpy ?
```

Interface web de jeu : `python3 tools/gui/server.py --port 8090` (stdlib seule). Sur fanorona-dev elle tourne
en service systemd `fanorona-gui` (port 8090) et utilise le binaire `./fanorona` et `checkpoints/*.nnue` du dépôt
tels quels : un `make` est pris en compte à la requête suivante, sans redémarrage.

Activer NNUE (UCI) : `setoption name EvalFile value checkpoints/net_v1.nnue` puis
`setoption name UseNNUE value true`. Désactivé par défaut (HCE inchangée).

Entraînement itératif (un réseau sert de "professeur" pour générer le corpus suivant) : activer NNUE
AVANT `gensfen`, donc par stdin et pas en argument de ligne de commande (sinon impossible d'envoyer le
`setoption` avant) :
```sh
printf 'setoption name EvalFile value checkpoints/net_v1.nnue\nsetoption name UseNNUE value true\ngensfen count 700000 depth 6 opening-plies 8 out data/gen2_1.txt\nquit\n' | ./fanorona
```

Suivi live (nodes/s, profondeur, eval...) pendant un match/SPRT : `tools/match.py` journalise en JSONL
(`/var/log/fanorona/<run_id>.jsonl` par défaut, désactivable avec `--no-live-log`), repris par Grafana Alloy
sur la VM `fanorona-dev` vers Loki/Grafana du homelab (dashboard "Fanorona - Recherche live"). Voir
`tools/metrics_logger.py`.

Build de débogage avec sanitizers :

```sh
g++ -g -O0 -std=c++17 -Isrc -fsanitize=address,undefined src/{bitboard,position,movegen,evaluate,nnue,tt,search,telo,uci}.cpp tests/test_main.cpp -o /tmp/t -pthread && /tmp/t
```

## Architecture

| Fichier | Rôle |
|---|---|
| `src/types.h` | constantes, `Color`, `Direction`, encodage `Move`, valeurs de mat |
| `src/bitboard.*` | `Neighbor[sq][dir]`, `shift()`, `capture_line()`, `capturers()` (détection rapide des captures) |
| `src/position.*` | `Position` (32 octets, copy-make), FEN, Zobrist, `Rules` (options de règles, dont `variant` et `vela`), `set_variant()`, `start_fen()` |
| `src/movegen.*` | `generate_moves()` (tours complets dédupliqués), `generate_detailed()` (avec notation), `parse_move()`, `perft()` ; `generate_vela()` pour la phase 1 de la vela |
| `src/telo.*` | Fanoron-Telo 3×3 (alignement, pose puis déplacement) : module à part, résolu par analyse rétrograde au démarrage (`Telo::init`), `go` parfait ou `go depth N` limité |
| `src/evaluate.*` | évaluation manuelle (HCE), du point de vue du camp au trait ; bascule vers `NNUE::evaluate()` si activé |
| `src/nnue.*` | inférence NNUE (charge un `.nnue`, forward pass 2×45→256 ReLU clippé→1, accumulateur incrémental par diff, W1 transposé, sortie AVX2) |
| `src/tt.*` | table de transposition, seaux de 2 entrées, générations |
| `src/search.*` | `Search::think()` : ID, aspiration, PVS, qsearch, NMP, RFP, LMR, killers, historique, temps |
| `src/uci.*` | boucle de commandes (`position`, `go`, `stop`, `setoption`, `d`, `moves`, `eval`, `status`, `perft`, `bench`, `play`, `gensfen`) |
| `tests/test_main.cpp` | tests des règles, perft, symétrie, clés, recherche |
| `tools/match.py` | matchs entre deux binaires : parties fixes ou arrêt SPRT (`--sprt`), suivi live optionnel |
| `tools/sprt.py` | test séquentiel SPRT (LLR gaussien sur le score moyen, cf. fishtest/cutechess-cli) |
| `tools/rules/reference.py`, `crosscheck.py` | générateur de coups de référence indépendant ; confrontation exhaustive avec le moteur |
| `tools/gui/server.py` | interface web : sert `index.html` + API JSON (`/api/state`, `/api/go`, `/api/eval` pour l'analyse), un processus moteur par requête ; niveaux de difficulté `LEVELS` (affaiblissement externe, le moteur n'a pas d'option de force) ; historique des parties `/api/games` (SQLite `data/gui_games.db`, hors git, non sauvegardé ailleurs que par la sauvegarde de la VM) |
| `tools/gui/index.html` | page unique façon lichess : plateau SVG (clic/glisser), pendules, coups navigables, analyse d'après-partie. `positions[k]` est rejoué localement avec des identifiants de pièces stables (animations) ; la logique de capture JS ne sert qu'à l'affichage, le moteur valide |
| `tools/gui/CHARTE.md` | charte graphique (typographie, palettes des 3 thèmes, plateaux bois/granite) : tout passe par les variables CSS de `:root` |
| `tools/gui/lessons.json` | leçons du tutoriel (chapitres, exercices : FEN de départ, objectif `reach`/`clear`/`captures`/`telo_win`/`win`, texte, indice) |
| `tools/gui/i18n.json` | tous les textes de l'interface, `fr` (référence) et `mg` (malgache) ; paramètres `{n}` |
| `tools/gui/elo.json` | Elo calibré de chaque réglage de `STRENGTHS` (généré par `tools/elo/calibrate.py`, résultats bruts dans `data/elo_calibration.jsonl`) — à régénérer si le moteur, le réseau ou l'échelle changent |
| `tools/gui/pwa/` | application installable : manifeste, service worker, icônes |
| `tools/gui/verify_lessons.py` | vérifie chaque exercice contre le moteur (solution et nombre minimal de coups `par` des exercices solo, position gagnante pour ceux contre le moteur) — à relancer après toute modification des leçons |
| `tools/gui/puzzles.json` | puzzles tactiques générés (FEN, ligne solution, coups acceptés par étape, classement, thèmes) |
| `tools/puzzles/gen_puzzles.py` | génération des puzzles depuis `gensfen` (faute aléatoire, coup unique entre « familles » de chaînes, second avis NNUE, `--verify-only` pour refiltrer) |
| `tools/metrics_logger.py` | journalisation JSONL des lignes UCI `info` + résultats, pour Grafana/Loki |
| `tools/nnue/train.py` | entraînement PyTorch du réseau NNUE à partir des données `gensfen` |
| `tools/nnue/verify.py` | vérifie que `src/nnue.cpp` donne EXACTEMENT le même score qu'une référence numpy |

## Conventions et invariants — à respecter

- **Cases** : `sq = y * 9 + x`, x = colonne a..i (0..8), y = rangée 1..5 (0..4). 45 bits utiles (`ALL_SQUARES`).
- **Points forts** : `(x + y)` pair ; seuls eux ont des diagonales (`STRONG_BB`). Tout décalage diagonal doit masquer par `STRONG_BB`.
- **Directions** : `EAST, NORTH_EAST, NORTH, NORTH_WEST, WEST, SOUTH_WEST, SOUTH, SOUTH_EAST` ; `opposite(d) = d ^ 4`.
- **Un coup = un tour complet**, encodé sur 64 bits : bits 0–44 = pièces capturées, 45–50 = départ, 51–56 = arrivée.
  Deux chaînes de même (départ, arrivée, captures) sont le même coup (dédupliqué). Le chemin n'est pas stocké :
  `move_to_string()` le reconstruit en régénérant les coups (lent, réservé à l'affichage).
- **Notation** : cases parcourues, chaque étape de capture suffixée `A` (approche) ou `W` (retrait). Paika : `e2e3`.
- **FEN** : rangées 5→1, `W`/`B`/chiffres, trait `w`/`b`, puis optionnellement rule50 et numéro de coup.
  Départ : `BBBBBBBBB/BBBBBBBBB/BWBW1BWBW/WWWWWWWWW/WWWWWWWWW w 0 1` (blancs en bas, jouent en premier).
- **Règles** : captures obligatoires ; chaîne par la même pièce, ni case déjà visitée, ni deux fois la même direction
  de suite ; arrêt de chaîne libre (option `MandatoryContinuation`). Plus de pièce ou plus de coup = défaite.
  Nulle : `NoCaptureLimit` (100 demi-coups) ou triple répétition (répétition simple dans l'arbre de recherche).
- **Scores** : centi-pions du point de vue du camp au trait ; `mate_in(ply)` / `mated_in(ply)` ; conversion
  `value_to_tt` / `value_from_tt` obligatoire pour les scores de mat dans la TT.
- **Historique de répétition** : `Search::think()` reçoit les clés de la partie, la dernière = position racine.
- **GUI** : ne jamais fermer stdin du moteur avant `bestmove` (fin de stdin = `quit` = arrêt immédiat de la recherche).
- **GUI multilingue** : aucun texte affiché en dur dans `index.html` — passer par `t("clé", {param})` (JS) ou
  `data-i18n` / `data-i18n-title` / `-placeholder` / `-aria` (HTML), et ajouter la clé en `fr` ET `mg` dans
  `i18n.json`. Les résultats de partie sont stockés en codes (`t`, `w`), traduits à l'affichage.
- Commentaires en français, identifiants en anglais, style Stockfish (clang-format Google, largeur 120).

## Valeurs de référence (régressions)

- perft depuis le départ : 1 → 5, 2 → 39, 3 → 724, 4 → 18026, 5 → 431830, 6 → 9204447.
- Fanoron-Dimy (5×5) : 1 → 5, 2 → 21, 3 → 202, 4 → 3469, 5 → 34608 (symétrie vérifiée, pas de référence externe).
- Fanoron-Telo (3×3) : 1 → 9, 2 → 72, 3 → 504, 4 → 3024, 5 → 15120 ; valeur exacte du départ : gain des Blancs en 9.
- `bench` : signature inchangée par l'ajout des variantes (876243 nœuds, profondeur 8) — la géométrie
  paramétrable ne doit rien changer au 9×5.
  (Confrontées à la référence indépendante `tools/rules/reference.py` jusqu'à la profondeur 5 : identiques.)
- Toute modification du générateur doit garder ces valeurs, sauf changement de règle volontaire.
- Toute modification de la recherche ou de l'évaluation change la signature `bench` : c'est normal, mais la
  valider par un match (`tools/match.py`) contre l'ancien binaire.

## Pièges connus

- **Explosion de la quiescence** : dans l'ouverture presque tous les coups sont des captures. Sans "stand pat",
  la profondeur 1 coûtait 21 M nœuds. Le stand pat (heuristique, non conforme strictement aux captures
  obligatoires) + delta pruning + `QS_MAX_DEPTH = 12` règlent le problème. Ne pas les retirer sans alternative.
- Null move / RFP sont désactivés dans les positions à capture (tous les coups y sont forcés).
- `MoveList` est sur la pile (`MAX_MOVES = 1024` × 8 octets par nœud) : attention si on augmente `MAX_PLY`.
- `Rules::*` sont des globales statiques : ne pas les modifier pendant une recherche.
- **Géométrie** : l'encodage des cases garde un pas de 9 (`sq = y * 9 + x`) quelle que soit la variante ; le 5×5
  est logé dans les colonnes a..e. Tout ce qui dépend de la taille passe par `Board` (`files`, `ranks`, `mask`) et
  par `ShiftFrom[d]` (cases ayant un voisin dans la direction d), recalculés par `set_variant()` : ne jamais
  réutiliser `ALL_SQUARES` comme « cases vides possibles », utiliser `Board::mask`.
- **MultiPV** (`SearchLimits::multiPV`, option UCI) : à la racine, les coups des lignes déjà trouvées sont exclus
  (`Worker::excluded`), et la racine d'une ligne secondaire n'écrit pas dans la TT. Avec MultiPV = 1 l'arbre et la
  sortie sont strictement ceux d'avant (signature `bench` inchangée) — à préserver.
- **Analyse en continu (GUI)** : une session serveur par onglet (`/api/analyse`), `go infinite` + `stop` ; le
  concierge coupe après 120 s ou 6 s sans interrogation. Chaque session tient un créneau de recherche.
- **NNUE** : le réseau est entraîné sur le 9×5 ; `evaluate()` l'ignore hors Fanoron-Tsivy (HCE à la place).
- **Vela** : la phase se déduit du plateau (`Position::vela_phase1()`, camp handicapé à plus de 5 pions), pas d'état
  supplémentaire dans `Position`. La quiescence doit rester consciente de la vela (le camp handicapé ne capture
  jamais, le bénéficiaire sans prise a perdu).
- La recherche tourne dans un `std::thread` lancé par `go` ; `stop` met `Search::stopSignal`.

## État actuel

- ~2,5 M nœuds/s, profondeur 14 en ~28 s depuis la position initiale (1 thread).
- Évaluation : matériel (100), points forts, connectivité, mobilité, menaces, bonus de simplification, tempo.
  Poids non réglés.

## Feuille de route (par priorité)

1. ~~**Validation des règles**~~ **fait** (2026-10-01) : `tools/rules/reference.py`, générateur de coups indépendant et
   naïf (coordonnées, ensembles Python, aucun bitboard), écrit d'après les règles ; `tools/rules/crosscheck.py` compare
   la liste EXACTE des coups notés du moteur (`moves`) à la référence : 60 000 positions (auto-jeu et parties
   aléatoires, Tsivy et Dimy, arrêt libre et continuation obligatoire), ~454 000 coups, et perft 1..5 — aucune
   divergence. Point de règle tranché par l'ICGA : revenir sur son point de DÉPART pendant une chaîne est interdit
   (exemple officiel « f4-e4W-e3A-f4A » refusé), comme le moteur l'applique. À relancer après toute modification du
   générateur : `python3 tools/rules/crosscheck.py --positions 3000 --perft 4` (~12 s).
2. ~~**Tests SPRT**~~ **fait** (2026-09-27) : `tools/match.py --sprt` (LLR gaussien, `tools/sprt.py`), ouvertures
   aléatoires déjà existantes, suivi live JSONL. Parallélisme (plusieurs parties en simultané) pas encore fait.
3. **Texel tuning** : générer des positions d'auto-jeu avec résultats, optimiser les poids de `evaluate.cpp`.
4. **NNUE** — en cours (2026-09-27) :
   - ~~générateur de données~~ **fait** : `./fanorona gensfen` (auto-jeu, `depth`/`opening-plies`/`count`/`out`),
     format `<fen>|<score_cp>|<wdl>` (voir `cmd_gensfen` dans `src/uci.cpp`) ; un vrai corpus de 10M positions
     généré sur fanorona-dev (5 processus en parallèle, depth 6, opening-plies 8, ~25 min, `data/gensfen.txt`,
     gitignored) ;
   - ~~entraînement PyTorch~~ **fait, entraîné sur les 10M positions** : `tools/nnue/train.py`, entrées 2×45,
     couche cachée 256 ReLU clippé, cible = mélange score de recherche (sigmoïde) / résultat réel ; export
     binaire float32 provisoire (`export_weights`, non quantifié). 15 epochs, val_loss 0.00843 -> 0.00759
     (converge proprement, `checkpoints/net_v1.nnue`, gitignored — à régénérer, pas commité).
     **Piège vécu et corrigé** : `SfenDataset` ne doit PAS être un `torch.utils.data.Dataset` consommé via
     `DataLoader` — à 10M échantillons, `__getitem__` par échantillon (+ collate par défaut) coûte des dizaines
     de minutes d'overhead Python pur, et `num_workers>0` duplique le dataset en mémoire par worker (comptage
     de références qui casse le copy-on-write du fork) -> OOM en quelques minutes (vécu deux fois). Fix : tout
     précalculer en tableaux numpy contigus une fois, puis batcher par slicing numpy direct (`iter_batches`),
     sans DataLoader. Pense aussi à `flush=True` sur les `print` d'epoch (stdout redirigé vers un fichier =
     bufferisé par bloc, pas par ligne : sans flush, rien n'apparaît avant la fin du run).
   - ~~inférence C++~~ **fait** (2026-09-27) : `src/nnue.*`, option UCI `UseNNUE` + `EvalFile` (défaut
     désactivé, HCE inchangée). `evaluate()` (src/evaluate.cpp) bascule automatiquement vers
     `NNUE::evaluate()` si activé — aucun site d'appel à changer.
   - ~~accumulateur incrémental~~ **fait, mais PAS comme prévu initialement ci-dessus** (2026-09-27) :
     surtout NE PAS mettre les accumulateurs dans `Position` (l'idée de départ) — `Position` est copiée à
     chaque nœud, y compris par `perft`/le movegen qui n'utilisent jamais NNUE ; l'alourdir de ~2×256
     flottants aurait cassé la vitesse de perft pour tout le monde, NNUE actif ou non. À la place :
     `NNUE::evaluate(pos)` garde la signature `evaluate(const Position&)` inchangée et maintient en interne
     (état global dans nnue.cpp) le DERNIER accumulateur calculé ; à chaque appel, diff XOR entre les
     bitboards de `pos` et ceux du dernier appel pour ne mettre à jour que les cases qui ont changé (un coup,
     même une chaîne de captures, est une modification atomique des bitboards — le diff reconstruit
     exactement ce qui a changé sans avoir besoin de connaître "le coup"). Toujours deux accumulateurs, un
     par perspective de couleur absolue : `accum[c] = b1 + W_own @ pieces(c) + W_opp @ pieces(~c)` (voir le
     commentaire détaillé dans `nnue.cpp` pour le détail own/opp par accumulateur). Zéro changement à
     `Position`, au copy-make, à `search.cpp` ou au movegen : risque totalement isolé à `nnue.cpp`.
     **Limite connue** : cache global, pas thread-safe — à revoir si Lazy SMP (point 5) est implémenté.
   - **Validation** : `make test` + `perft 5` inchangés (HCE non touchée), build ASan/UBSan propre y compris
     sous recherche réelle (`bench`/`go`, avec `ucinewgame` pour exercer la remise à zéro du cache) et
     `tools/nnue/verify.py` confirme une correspondance EXACTE (1000/1000) entre le C++ et une référence
     numpy qui réimplémente le forward pass de `train.py` SANS aucune logique incrémentale (donc un bug de
     diff/cache aurait divergé) — c'est la vérification qui compte vraiment, un simple "ça compile et ça ne
     crash pas" n'aurait rien dit sur un bug d'encodage ou de cache silencieux.
   - **Vitesse mesurée** (bench profondeur 8, comparaison directe A/B sur le même run) : ~450-470k nps avec
     l'accumulateur incrémental contre ~280-300k nps en forçant un recalcul complet à chaque appel — un vrai
     gain d'environ 1,5×, mais nettement moins que le "quasi gratuit" espéré. Raison : l'ordre de visite des
     nœuds en alpha-bêta n'est pas une simple marche DFS où deux appels `evaluate()` consécutifs sont presque
     toujours à 1 coup d'écart (contrairement à un accumulateur façon Stockfish poussé/dépilé en même temps
     que la recherche elle-même) — le diff est donc souvent plus grand qu'entre deux positions vraiment
     adjacentes. Reste ~5,5× plus lent que la HCE (2,5M nps). Pour aller plus loin il faudrait vraiment fileter
     l'accumulateur à travers la récursion de `search()`/`qsearch()` (garantit un diff à 1 coup à chaque
     appel) — plus invasif (signatures des fonctions de recherche à changer), pas fait ici : le gain
     mesuré (1,5×) à faible risque a semblé le meilleur rapport effort/risque pour cette passe.
   - ~~SPRT de confirmation contre la HCE~~ **fait, résultat positif** (2026-09-27) : `tools/match.py` a
     gagné des options UCI par moteur (`--engineN-opts`, ex. `UseNNUE=true,EvalFile=...`) pour comparer NNUE
     et HCE avec le même binaire. `--sprt --elo0 -30 --elo1 30 --movetime 100` (bornes larges pour une
     réponse rapide vu qu'on ne savait pas dans quel sens irait l'écart) : **H1 acceptée après seulement 90
     parties** (LLR +2,958, franchit la borne +2,944), score final W35 D31 L24 (~56,1%) pour NNUE — donc
     NNUE bat la HCE d'au moins 30 Elo à ce contrôle de temps. Net encourageant pour un réseau entraîné sur
     un seul corpus auto-jeu profondeur 6 sans itération/renforcement.
     **Nuance importante** : ce test mesure la force pratique à temps de réflexion égal (100 ms/coup), pas la
     qualité de l'évaluation à profondeur égale — NNUE cherche ~5,5× moins profond (accumulateur incrémental,
     voir plus haut) et gagne quand même, ce qui est en réalité un signal plutôt FORT en faveur de la qualité
     de l'éval NNUE elle-même (elle compense largement le handicap de profondeur). À revalider avec des
     bornes plus fines (ex. elo0=0/elo1=10) et/ou d'autres contrôles de temps si on veut un chiffre d'Elo
     plus précis qu'un simple "≥30".
   - ~~entraînement itératif~~ **fait, résultat positif** (2026-09-27) : `net_v1` (entraîné sur de l'auto-jeu
     guidé par la HCE) a servi de "professeur" pour générer un DEUXIÈME corpus — `./fanorona gensfen` avec
     `UseNNUE`/`EvalFile` activés en amont (via stdin, pas en argument de ligne de commande : sinon pas
     moyen d'envoyer le `setoption` avant `gensfen`) — 5 processus en parallèle, 3,5M positions (`data/
     gensfen_gen2.txt`, ~87 min à cause du nps plus faible de NNUE). `net_v2` entraîné dessus (mêmes
     hyperparamètres que v1), vérifié par `verify.py`. **SPRT net_v2 vs net_v1** (mêmes bornes larges
     -30/+30, `--engine1-opts`/`--engine2-opts` pour charger deux `.nnue` différents sur le même binaire) :
     H1 acceptée après 189 parties (LLR +3,110), score W75 D52 L62 (~53,4%) pour `net_v2` — un cycle de
     renforcement suffit déjà à mesurablement dépasser le réseau de départ. Cohérent avec la mécanique
     attendue : un professeur plus fort (v1, déjà meilleur que la HCE) génère de meilleures données
     d'entraînement que le professeur HCE original.
     **Piège rencontré en cours de route** : `tools/nnue/verify.py` comparait par égalité stricte
     (`p != c`) alors que numpy (BLAS) et la boucle C++ naïve n'accumulent pas les 256 termes dans le même
     ordre — sur une valeur tombant à ~1e-4 d'une frontière d'arrondi ça peut arrondir différemment d'un
     côté ou de l'autre (vécu : 1/1000 sur `net_v2`, écart d'1 cp). Pas un bug d'éval (une vraie divergence
     donnerait des écarts systématiques bien plus grands) — `verify.py` tolère maintenant ±1cp par défaut
     (`--tol`).
   - **Calcul distribué sur 3 nœuds PVE** (2026-09-28) : `gensfen` est embarrassingly parallel, donc on peut
     répartir sur tout le cluster plutôt qu'un seul hôte. Deux nouveaux LXC légers (Debian 13, juste le
     binaire C++, pas de Python/PyTorch) : **fanorona-c1** (VMID 3190, pve1, 5 vCPU, 10.10.10.190) et
     **fanorona-c3** (VMID 3191, pve3, 3 vCPU, 10.10.10.191), `onboot=0` volontaire (nœuds de calcul
     ponctuels, à démarrer manuellement avant un cycle). Avec fanorona-dev (6 vCPU, pve2), ça fait ~14 vCPU
     au lieu de 6 pour générer des données. Workflow : cloner/build sur chaque nœud, copier le `.nnue`
     professeur, lancer `gensfen` en parallèle sur les 3 (setoption NNUE par stdin comme d'habitude),
     rapatrier les fichiers des LXC vers fanorona-dev (seul hôte avec le venv PyTorch) par `scp`, concaténer,
     entraîner comme d'habitude. Seule la génération de données est distribuée — l'entraînement PyTorch
     lui-même reste sur une seule machine (rapide, modèle minuscule, pas besoin de distribuer).
   - ~~2e cycle de renforcement (`net_v2` -> `gensfen_gen3` -> `net_v3`)~~ **fait** (2026-09-28) : 7,7M
     positions générées en ~1h40 sur les 3 nœuds (`net_v2` comme professeur), `net_v3` entraîné dessus
     (val_loss 0,00895, le meilleur des 3 générations), vérifié par `verify.py`. **SPRT net_v3 vs net_v2**
     (mêmes bornes -30/+30) : H1 acceptée après 343 parties (LLR +3,116), score W120 D115 L108 (~51,75%) —
     un gain réel mais net_tement plus petit et plus lent à confirmer que net_v2 vs net_v1 (189 parties,
     marge plus large). **Rendements décroissants confirmés** (pas juste supposés) : chaque cycle de
     renforcement supplémentaire semble apporter de moins en moins, cohérent avec la théorie (le professeur
     s'améliore, mais l'écart entre "bon professeur" et "encore meilleur professeur" se réduit).
   - ~~accélération de l'inférence~~ **fait, ×4** (2026-10-01) : 420k -> ~1,7M nps au `bench 8` NNUE, **même
     signature** (1 121 922 nœuds, recherche identique). Le goulot n'était PAS la taille du diff de
     l'accumulateur (hypothèse ci-dessus, démentie par la mesure) mais l'absence de vectorisation :
     (1) W1 stocké `[256][90]` (format de nn.Linear) -> chaque colonne lue par sauts de 90 flottants ; transposé
     au chargement en `[90][256]` (`g_w1t`), les mises à jour deviennent contiguës et vectorisées (×1,5) ;
     (2) la couche de sortie était une réduction flottante que GCC ne vectorise pas sans `-ffast-math` (256
     multiplications-additions en chaîne) : réécrite en AVX2/FMA explicite (`immintrin.h`, deux accumulateurs
     de 8), avec repli portable à 8 sommes partielles (×2,5). Vérifier avec
     `g++ ... -fopt-info-vec-all src/nnue.cpp` qu'une boucle chaude est bien vectorisée avant de chercher plus loin.
     **Mesuré et abandonné** : un accumulateur par ply (réutiliser le parent ou le dernier frère) fait passer de
     12,2 à 10,1 colonnes mises à jour par évaluation — le reste est le coût propre des coups (une capture touche
     2 colonnes par pièce prise), d'où aucun gain mesurable (A/B à ±1 %) : pas la peine de fileter l'accumulateur
     dans la récursion. NNUE est maintenant à ~70 % de la vitesse de la HCE (contre 18 %).
     SPRT nouveau binaire contre l'ancien (même net_v3, 100 ms/coup) : H1 acceptée après 229 parties (LLR +3,00, bornes 0/+30), W91 D79 L59 = 57 % soit environ +49 Elo.
   - **reste à faire** : quantification int16 (2× plus de voies par instruction, gain borné : l'évaluation
     n'est plus qu'une part minoritaire du temps par nœud) ; un 4e cycle de renforcement (gains décroissants
     mesurés) ; recalibrer l'Elo de l'interface (`tools/elo/calibrate.py`) après tout gain de force.
5. **Lazy SMP** : option `Threads`, TT partagée (entrées rendues sûres par XOR clé/données).
6. Améliorations de recherche : singular extensions, IIR, history de continuation, meilleur ordre des captures.
7. Bases de finales (peu de pièces), livre d'ouvertures.

## Méthode de travail

- **Tenir `JOURNAL.md` à jour** : une entrée par étape significative (moteur, réseau, règles, interface), avec le
  résultat mesuré (SPRT, bench, perft) et le commit, dans le même commit ou juste après.

- Toujours : `make && make test` avant de committer ; `perft 5` inchangé si le générateur est touché.
- Changement de force (recherche/éval) : garder l'ancien binaire (`cp fanorona fanorona-old`) et lancer un match.
- Commits petits et descriptifs, en français.
