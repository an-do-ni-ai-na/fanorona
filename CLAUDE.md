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
python3 tools/match.py ./fanorona ./fanorona-old --games 20 --movetime 100   # auto-jeu
```

Build de débogage avec sanitizers :

```sh
g++ -g -O0 -std=c++17 -Isrc -fsanitize=address,undefined src/{bitboard,position,movegen,evaluate,tt,search,uci}.cpp tests/test_main.cpp -o /tmp/t -pthread && /tmp/t
```

## Architecture

| Fichier | Rôle |
|---|---|
| `src/types.h` | constantes, `Color`, `Direction`, encodage `Move`, valeurs de mat |
| `src/bitboard.*` | `Neighbor[sq][dir]`, `shift()`, `capture_line()`, `capturers()` (détection rapide des captures) |
| `src/position.*` | `Position` (32 octets, copy-make), FEN, Zobrist, `Rules` (options de règles) |
| `src/movegen.*` | `generate_moves()` (tours complets dédupliqués), `generate_detailed()` (avec notation), `parse_move()`, `perft()` |
| `src/evaluate.*` | évaluation manuelle (HCE), du point de vue du camp au trait |
| `src/tt.*` | table de transposition, seaux de 2 entrées, générations |
| `src/search.*` | `Search::think()` : ID, aspiration, PVS, qsearch, NMP, RFP, LMR, killers, historique, temps |
| `src/uci.*` | boucle de commandes (`position`, `go`, `stop`, `setoption`, `d`, `moves`, `eval`, `status`, `perft`, `bench`, `play`) |
| `tests/test_main.cpp` | tests des règles, perft, symétrie, clés, recherche |
| `tools/match.py` | matchs entre deux binaires |

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
2. **Tests SPRT** : étendre `tools/match.py` (ouvertures variées, Elo ± marge, arrêt SPRT, parallélisme).
3. **Texel tuning** : générer des positions d'auto-jeu avec résultats, optimiser les poids de `evaluate.cpp`.
4. **NNUE** :
   - générateur de données : commande `gensfen`-like (auto-jeu à faible profondeur, positions calmes, score + résultat) ;
   - entraînement PyTorch dans `tools/nnue/` : entrées 2 × 45 (pièces du camp au trait / adverses), une couche
     cachée ~256 (accumulateur), sorties clippées ReLU, quantification int16/int8 ;
   - inférence C++ incrémentale dans `src/nnue/` (mise à jour de l'accumulateur dans `do_move`), option `UseNNUE`.
5. **Lazy SMP** : option `Threads`, TT partagée (entrées rendues sûres par XOR clé/données).
6. Améliorations de recherche : singular extensions, IIR, history de continuation, meilleur ordre des captures.
7. Bases de finales (peu de pièces), livre d'ouvertures.

## Méthode de travail

- Toujours : `make && make test` avant de committer ; `perft 5` inchangé si le générateur est touché.
- Changement de force (recherche/éval) : garder l'ancien binaire (`cp fanorona fanorona-old`) et lancer un match.
- Commits petits et descriptifs, en français.
