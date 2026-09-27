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

Activer NNUE (UCI) : `setoption name EvalFile value checkpoints/net_v1.nnue` puis
`setoption name UseNNUE value true`. Désactivé par défaut (HCE inchangée).

Suivi live (nodes/s, profondeur, eval...) pendant un match/SPRT : `tools/match.py` journalise en JSONL
(`/var/log/fanorona/<run_id>.jsonl` par défaut, désactivable avec `--no-live-log`), repris par Grafana Alloy
sur la VM `fanorona-dev` vers Loki/Grafana du homelab (dashboard "Fanorona - Recherche live"). Voir
`tools/metrics_logger.py`.

Build de débogage avec sanitizers :

```sh
g++ -g -O0 -std=c++17 -Isrc -fsanitize=address,undefined src/{bitboard,position,movegen,evaluate,nnue,tt,search,uci}.cpp tests/test_main.cpp -o /tmp/t -pthread && /tmp/t
```

## Architecture

| Fichier | Rôle |
|---|---|
| `src/types.h` | constantes, `Color`, `Direction`, encodage `Move`, valeurs de mat |
| `src/bitboard.*` | `Neighbor[sq][dir]`, `shift()`, `capture_line()`, `capturers()` (détection rapide des captures) |
| `src/position.*` | `Position` (32 octets, copy-make), FEN, Zobrist, `Rules` (options de règles) |
| `src/movegen.*` | `generate_moves()` (tours complets dédupliqués), `generate_detailed()` (avec notation), `parse_move()`, `perft()` |
| `src/evaluate.*` | évaluation manuelle (HCE), du point de vue du camp au trait ; bascule vers `NNUE::evaluate()` si activé |
| `src/nnue.*` | inférence NNUE (charge un `.nnue`, forward pass 2×45→256 ReLU clippé→1, PAS ENCORE incrémental) |
| `src/tt.*` | table de transposition, seaux de 2 entrées, générations |
| `src/search.*` | `Search::think()` : ID, aspiration, PVS, qsearch, NMP, RFP, LMR, killers, historique, temps |
| `src/uci.*` | boucle de commandes (`position`, `go`, `stop`, `setoption`, `d`, `moves`, `eval`, `status`, `perft`, `bench`, `play`, `gensfen`) |
| `tests/test_main.cpp` | tests des règles, perft, symétrie, clés, recherche |
| `tools/match.py` | matchs entre deux binaires : parties fixes ou arrêt SPRT (`--sprt`), suivi live optionnel |
| `tools/sprt.py` | test séquentiel SPRT (LLR gaussien sur le score moyen, cf. fishtest/cutechess-cli) |
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
- Commentaires en français, identifiants en anglais, style Stockfish (clang-format Google, largeur 120).

## Valeurs de référence (régressions)

- perft depuis le départ : 1 → 5, 2 → 39, 3 → 724, 4 → 18026, 5 → 431830, 6 → 9204447.
  (Seule la profondeur 1 est vérifiée à la main ; pas de référence publiée confrontée.)
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
- La recherche tourne dans un `std::thread` lancé par `go` ; `stop` met `Search::stopSignal`.

## État actuel

- ~2,5 M nœuds/s, profondeur 14 en ~28 s depuis la position initiale (1 thread).
- Évaluation : matériel (100), points forts, connectivité, mobilité, menaces, bonus de simplification, tempo.
  Poids non réglés.

## Feuille de route (par priorité)

1. **Validation des règles** contre une source de référence (perft publié ou autre implémentation) si disponible.
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
   - **reste à faire** : quantification int16/int8 réelle (le format d'export actuel — float32 — est un
     contrat de départ, pas figé) ; l'accumulateur fileté dans la récursion de recherche (fermerait
     l'essentiel de l'écart de profondeur avec la HCE, cf. note vitesse ci-dessus) devient plus intéressant
     maintenant qu'on sait que NNUE apporte un vrai gain d'Elo ; entraînement itératif (le réseau actuel n'a
     vu qu'un seul cycle d'auto-jeu HCE-guidé, pas de renforcement via ses propres parties).
5. **Lazy SMP** : option `Threads`, TT partagée (entrées rendues sûres par XOR clé/données).
6. Améliorations de recherche : singular extensions, IIR, history de continuation, meilleur ordre des captures.
7. Bases de finales (peu de pièces), livre d'ouvertures.

## Méthode de travail

- Toujours : `make && make test` avant de committer ; `perft 5` inchangé si le générateur est touché.
- Changement de force (recherche/éval) : garder l'ancien binaire (`cp fanorona fanorona-old`) et lancer un match.
- Commits petits et descriptifs, en français.
