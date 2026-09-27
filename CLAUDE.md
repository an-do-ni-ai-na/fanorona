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
   - ~~inférence C++~~ **fait, mais pas encore incrémentale** (2026-09-27) : `src/nnue.*`, option UCI `UseNNUE`
     + `EvalFile` (défaut désactivé, HCE inchangée). `evaluate()` (src/evaluate.cpp) bascule automatiquement
     vers `NNUE::evaluate()` si activé — aucun site d'appel à changer. Validé : `make test` + `perft 5`
     inchangés, build ASan/UBSan propre (y compris sous recherche réelle, `bench`/`go`), et surtout
     `tools/nnue/verify.py` confirme une correspondance EXACTE (300/300) entre le C++ et une référence numpy
     réimplémentant le forward pass de `train.py` — c'est la vérification qui compte vraiment ici, un simple
     "ça compile et ça ne crash pas" n'aurait rien dit sur un bug d'encodage silencieux (ex. inverser
     own/opp, ou l'ordre des cases). Recalcule tout depuis les bitboards à CHAQUE appel d'`evaluate()` (pas
     d'accumulateur) : mesuré ~473k nps en bench profondeur 8 avec NNUE contre ~2,5M nps en HCE — net ralenti
     mais fonctionnel, cohérent avec le choix volontaire de prioriser la justesse avant la vitesse.
   - **reste à faire** : accumulateur incrémental (mis à jour dans `Position::do_move`, cf. note technique
     ci-dessous) pour retrouver une vitesse proche de la HCE, puis quantification int16/int8 réelle (le format
     d'export actuel — float32 — est un contrat de départ, pas figé). Ordre logique avant tout SPRT de
     confirmation contre la HCE : incrémental d'abord (revalider avec `verify.py` + ASan après, la logique
     d'accumulation est plus facile à casser que le forward pass complet), *puis* seulement lancer le SPRT —
     lancer un SPRT sur la version lente actuelle gâcherait juste du temps de calcul pour rien.
   - **Note technique pour l'accumulateur incrémental** : l'encodage est *relatif* au camp au trait (own/opp,
     pas figé par couleur), donc un accumulateur unique ne suffit pas — il faut deux accumulateurs, un par
     perspective de couleur absolue : `accumulator[c] = b1 + W_own @ pieces(c) + W_opp @ pieces(~c)`. En clair
     `accumulator[c]` est "l'accumulateur tel qu'il serait si c'était le tour de `c`", donc à l'évaluation on
     lit directement `accumulator[pos.sideToMove]`. Sur un coup joué par `us` (capturant des pièces de
     `~us`) : dans `accumulator[us]` la pièce qui bouge est "own" (colonnes `W_own`) et les captures sont
     "opp" (colonnes `W_opp`, à retirer) ; dans `accumulator[~us]` c'est l'inverse (pièce qui bouge = "opp",
     captures = "own"). Les deux accumulateurs (2×256 flottants/int16) doivent voyager avec `Position` dans le
     copy-make (comme `byColor`/`key`), pas être recalculés à chaque nœud.
5. **Lazy SMP** : option `Threads`, TT partagée (entrées rendues sûres par XOR clé/données).
6. Améliorations de recherche : singular extensions, IIR, history de continuation, meilleur ordre des captures.
7. Bases de finales (peu de pièces), livre d'ouvertures.

## Méthode de travail

- Toujours : `make && make test` avant de committer ; `perft 5` inchangé si le générateur est touché.
- Changement de force (recherche/éval) : garder l'ancien binaire (`cp fanorona fanorona-old`) et lancer un match.
- Commits petits et descriptifs, en français.
