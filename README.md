# Fanorona-Engine

Moteur de jeu pour le **Fanorona** (jeu traditionnel malgache, plateau 9×5),
écrit en C++17 et construit sur les mêmes principes que **Stockfish** :
bitboards, recherche alpha-bêta PVS avec approfondissement itératif, table de
transposition à hachage Zobrist, quiescence, élagages et réductions
modernes, et un protocole texte inspiré de l'UCI.

## Compilation

```sh
make            # construit ./fanorona
make test       # tests des règles, de la génération des coups et de la recherche
make bench      # test de performance (nœuds/seconde)
```

## Utilisation

```sh
./fanorona
position startpos moves d3e3A
d
go movetime 2000
```

Ou jouer directement contre le moteur :

```sh
./fanorona "play w 2000"     # vous avez les blancs, 2 s de réflexion par coup
```

### Commandes

| Commande | Rôle |
|---|---|
| `uci`, `isready`, `ucinewgame`, `quit` | comme en UCI |
| `position startpos \| fen <fen> [moves ...]` | fixe la position |
| `go [depth N] [movetime ms] [wtime/btime/winc/binc ms] [movestogo N] [nodes N] [infinite]` | lance la recherche (répond `bestmove ...`) |
| `stop` | interrompt la recherche |
| `setoption name Hash value 256` | taille de la table de transposition (Mo) |
| `setoption name MandatoryContinuation value true` | variante : séquence de captures obligatoire jusqu'au bout |
| `setoption name NoCaptureLimit value 100` | nulle après N demi-coups sans capture |
| `d`, `moves`, `eval`, `status` | affichage, coups légaux, évaluation, état de la partie |
| `perft N` | comptage des feuilles (validation du générateur) |
| `bench [N]` | recherche à profondeur N sur un jeu de positions fixe |
| `play [w\|b] [ms]` | partie interactive dans le terminal |

### Notation

- Cases `a1` … `i5` (colonnes a–i de gauche à droite, rangées 1–5 de bas en haut ;
  les blancs `W` commencent en bas et jouent en premier).
- Un coup est la liste des cases parcourues ; chaque étape de capture est
  suivie de `A` (**approche**) ou `W` (**retrait**, *withdrawal*).
  Exemples : `e2e3A`, `d3e3W`, `c1d1Ad2A` (deux captures successives),
  `e2e3` (déplacement simple / *paika*).
- En saisie, les lettres `A`/`W` peuvent être omises si le coup n'est pas ambigu.

FEN : 5 rangées de haut (5) en bas (1), `W`/`B`/chiffres, puis le trait `w`/`b`
et optionnellement le compteur sans capture et le numéro de coup. Position initiale :

```
BBBBBBBBB/BBBBBBBBB/BWBW1BWBW/WWWWWWWWW/WWWWWWWWW w 0 1
```

## Règles implémentées

- 45 points ; les lignes orthogonales relient tous les points, les diagonales
  seulement les points « forts » (x + y pair).
- Capture par **approche** (on s'avance vers une pièce adverse) ou par
  **retrait** (on s'éloigne d'une pièce adverse) : toute la ligne contiguë
  de pièces adverses dans cette direction est retirée. Si un déplacement
  permet les deux, le joueur choisit.
- Les captures sont **obligatoires** ; le *paika* (déplacement sans capture)
  n'est permis que si aucune capture n'existe.
- Après une capture, la même pièce peut continuer à capturer, sans
  repasser par un point déjà visité pendant le tour ni se déplacer deux fois
  de suite dans la même direction. Le joueur peut s'arrêter quand il veut
  (option `MandatoryContinuation` pour la variante stricte).
- Fin de partie : le camp qui n'a plus de pièce (ou plus aucun coup) a perdu.
  Nulle par triple répétition ou après `NoCaptureLimit` demi-coups sans capture.

## Architecture (et parallèle avec Stockfish)

| Fichier | Contenu | Équivalent Stockfish |
|---|---|---|
| `src/types.h` | types, encodage d'un coup sur 64 bits | `types.h` |
| `src/bitboard.*` | plateau 45 cases dans un `uint64_t`, décalages, détection rapide des captures | `bitboard.*` |
| `src/position.*` | position, FEN, clés Zobrist incrémentales, *copy-make* | `position.*` |
| `src/movegen.*` | génération des tours complets (DFS des chaînes de captures, dédupliquées), notation, perft | `movegen.*` |
| `src/evaluate.*` | évaluation manuelle : matériel, points forts, mobilité, menaces, simplification | `evaluate.*` (HCE historique) |
| `src/tt.*` | table de transposition à seaux de 2 entrées, générations | `tt.*` |
| `src/search.*` | approfondissement itératif, fenêtres d'aspiration, PVS, quiescence sur les captures, null move, reverse futility, LMR, killers, historique, extension des coups forcés, gestion du temps | `search.*` |
| `src/uci.*` | protocole texte, `perft`, `bench`, `play` | `uci.*` |
| `tools/match.py` | matchs en auto-jeu entre deux binaires | *fishtest* (en miniature) |

Choix spécifiques au Fanorona :

- **Un coup = un tour complet.** Une chaîne de captures est générée en entier
  par la même pièce ; les chaînes menant au même résultat (départ, arrivée,
  pièces capturées) sont fusionnées, ce qui réduit fortement le facteur de
  branchement. Le coup tient alors dans 64 bits, idéal pour la table de
  transposition.
- **Quiescence sur les captures obligatoires** : une position est « calme »
  lorsque le camp au trait n'a aucune capture.

## Pistes pour aller plus loin

1. **Réglage automatique de l'évaluation** (Texel tuning) sur des parties
   d'auto-jeu.
2. **NNUE** : générer des millions de positions évaluées par auto-jeu, entraîner
   un petit réseau (entrées : 2 × 45 cases) et remplacer `evaluate()` par une
   inférence incrémentale, comme Stockfish depuis la version 12.
3. **Lazy SMP** : plusieurs threads partageant la table de transposition.
4. **Tests SPRT** automatisés avec `tools/match.py` pour valider chaque
   amélioration.
5. Bases de finales (le Fanorona a été résolu : nulle avec un jeu parfait,
   Schadd et al., 2008).
