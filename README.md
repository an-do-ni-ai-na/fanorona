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

### Interface web

```sh
python3 tools/gui/server.py --port 8090    # puis http://localhost:8090/
```

Interface inspirée de lichess : pendules (cadences 1+0 à 30+0 ou illimité), cartes des joueurs, liste des
coups navigable (flèches du clavier, Origine/Fin), glisser-déposer ou clic, sons. Menu Apparence : thèmes
Papier (clair), Ardoise (sombre) et Veille (nuit, éventuellement automatique de 22 h à 7 h), plateau bois ou granite
(charte graphique : `tools/gui/CHARTE.md`). On joue
contre le moteur (six niveaux, voir ci-dessous), à deux sur le même écran, ou on regarde le moteur jouer contre
lui-même ; reprise de coup, indice, abandon, revanche. En fin de partie, « Analyser » évalue chaque position
(`/api/eval`) : courbe d'avantage, imprécisions `?!`, erreurs `?` et gaffes `??` (perte de chances de gain
≥ 0,1 / 0,2 / 0,3 comme sur lichess), perte moyenne en centipions, meilleur coup affiché sur le plateau.
« Apprendre de ses erreurs » rejoue ensuite chaque erreur ou gaffe du joueur : il faut trouver un coup
aussi bon que celui du moteur (moins de 0,1 de chances de gain perdues), avec la solution à la demande.
Elo : la force du moteur est calibrée (13 réglages, `tools/gui/elo.json`) ; les niveaux affichent leur Elo, un
curseur choisit une force précise, et chaque profil a un Elo mis à jour après chaque partie classée (Fanoron-Tsivy
contre l'ordinateur, sans vela, niveau calibré) ; « Partie équilibrée » propose le réglage le plus proche.
Application installable (PWA) : manifeste, service worker (coquille en cache, réseau d abord) et icônes dans
`tools/gui/pwa/` ; menu du profil > « Installer l application » (Android/Chrome/Edge), ou Partager > « Sur l écran
d accueil » sur iPhone. Le site doit être servi en HTTPS (c est le cas via nginx-proxy).
Analyse en continu (panneau de gauche, page Jouer) : le moteur cherche sans fin sur la position affichée et montre
ses 1 à 3 meilleures lignes (flèches sur le plateau, barre d'évaluation) ; relancée à chaque changement de position,
arrêtée après 2 minutes ; indisponible pendant une partie en cours contre l'ordinateur.
Profils (puce en haut à droite) : chacun garde ses parties, son classement de puzzles et sa progression du
tutoriel, sur tous les appareils ; pas de mot de passe (service réservé au réseau local) ; mode invité. Page
d'accueil (départ rapide par niveau, reprise, puzzles, tutoriel, dernières parties, bilan), onglets Jouer / Puzzles /
Apprendre / Parties (barre d'onglets en bas sur mobile) ; la partie en cours est mise de côté pendant les puzzles
et le tutoriel.
Historique (« Parties ») : chaque partie terminée est enregistrée côté serveur (SQLite, `data/gui_games.db`,
option `--db`) avec son analyse éventuelle ; liste filtrable, bilan contre l'ordinateur par niveau, réouverture
pour revoir ou analyser, suppression. Pas de comptes : l'historique est commun à tous les appareils.
Interface en français ou en malgache (menu Apparence > Langue ; malgache par défaut si le navigateur l'est).
Textes dans `tools/gui/i18n.json`, leçons dans `lessons.json` (bloc `mg`) — la traduction malgache est à faire
relire par un locuteur natif.
Partage (bouton ⤴ sous la liste des coups) : lien contenant toute la partie, export FGN (avec analyse en option),
FEN de la position affichée, import d'un FGN, d'une liste de coups ou d'un lien.
Puzzles tactiques (« Puzzles ») : ~950 positions où un seul coup gagne nettement (chaînes, rafles, coups calmes,
combinaisons), classement Elo personnel, série de réussites ; générés depuis l'auto-jeu par
`tools/puzzles/gen_puzzles.py` (voir plus bas).
Tutoriel interactif (« Apprendre ») : 6 chapitres, du déplacement aux chaînes de captures, à la vela et au
Fanoron-Telo, avec exercices sur le plateau, indices et étoiles (leçons dans `tools/gui/lessons.json`).
Trois jeux (Fanoron-Tsivy 9×5, Dimy 5×5, Telo 3×3) et la partie *vela*, proposée au perdant en fin de
partie (« Vela ») puis enchaînée (« Vela suivante ») jusqu'à ce que le bénéficiaire gagne.
Six niveaux de difficulté (1–3 : chaque coup légal est évalué par une recherche courte puis tiré au sort, les
bons coups restant favoris ; 4–5 : profondeur plafonnée ; 6 : pleine force).
Saisie : cliquer la pièce puis chaque case d'arrivée ; si un déplacement permet approche **et** retrait,
cliquer la pièce à capturer (ou le bouton correspondant) ; « Arrêter la capture » (ou re-cliquer la pièce)
termine une chaîne. Aucune dépendance Python : le serveur lance un processus `./fanorona` éphémère par
requête (`position startpos moves ...`), le moteur reste seul juge des règles.

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
| `setoption name MultiPV value 3` | analyse : les N meilleures lignes (`info ... multipv k ...`), 1 à 8 |
| `setoption name Variant value tsivy\|dimy\|telo` | jeu : Fanoron-Tsivy 9×5 (défaut), Fanoron-Dimy 5×5, Fanoron-Telo 3×3 |
| `setoption name Vela value none\|white\|black` | partie *vela* (Fanoron-Tsivy) : camp bénéficiaire |
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

### Variantes

- **Fanoron-Dimy** (5×5) : mêmes règles, 12 pions chacun, centre vide
  (`BBBBB/BBBBB/BW1BW/WWWWW/WWWWW w`).
- **Fanoron-Telo** (3×3, source : Ludii d'après l'ethnographie de l'Imerina) : jeu d'alignement sans capture.
  3 pions chacun ; les joueurs posent d'abord leurs pions à tour de rôle (les Blancs commencent), puis les
  déplacent d'un point le long d'une ligne ; aligner ses 3 pions (rangée, colonne, diagonale) gagne, y compris
  pendant la pose. Notation : pose `b2`, déplacement `a1b2`. Le jeu est **entièrement résolu** au démarrage du
  moteur (analyse rétrograde) : avec un jeu parfait, **les Blancs gagnent en 9 demi-coups** (en commençant au
  centre). Un Fanoron-Telo « à captures » avec 4 pions par camp existe aussi dans certains jeux du commerce ;
  il n'est pas implémenté.
- **Vela** (Fanoron-Tsivy, d'après R. C. Bell, 1979) : partie à handicap jouée traditionnellement après une
  défaite, le perdant en étant le bénéficiaire. Le bénéficiaire commence. Phase 1, jusqu'à ce que l'autre camp
  n'ait plus que 5 pions (17 prises) : le bénéficiaire **doit** capturer exactement une pièce par tour — la plus
  proche sur la ligne, sans enchaînement — et perd s'il ne le peut pas ; l'autre camp ne joue que des *paika*,
  en laissant si possible une prise au bénéficiaire. Phase 2 : règles normales. Les velas se succèdent jusqu'à ce
  que le bénéficiaire gagne.

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

## Puzzles

```sh
python3 tools/puzzles/gen_puzzles.py data/gensfen_gen3.txt --count 1000 --workers 5   # -> tools/gui/puzzles.json
```

Comme sur lichess, un puzzle naît d'une faute : depuis une position d'auto-jeu, l'adversaire joue un coup au
hasard, puis le moteur évalue chaque réponse. On garde la position si un seul coup gagne nettement (au moins
+200 cp, 200 cp d'écart avec les autres coups) ; la solution est prolongée tant que le coup suivant reste unique
(jusqu'à 3 coups). Les coups d'une même chaîne arrêtée plus tôt forment une « famille » : l'unicité se juge entre
familles, et s'arrêter trop tôt dans la bonne chaîne est une erreur. Second avis : le réseau NNUE (profondeur 10)
doit jouer un coup accepté, sinon le puzzle est écarté (~3 %). La difficulté est une estimation (nombre de coups,
longueur de chaîne, coup calme, solution en plusieurs coups), à recalibrer d'après les résultats réels.

## Format de partie FGN

Il n'existe pas de format standard pour noter une partie complète de Fanorona. L'interface utilise le **FGN**
(*Fanorona Game Notation*), calqué sur le PGN des échecs :

```
[Event "Partie contre l'ordinateur"]
[Date "2026.09.30"]
[White "Vous"]
[Black "Fanorona-Engine"]
[Result "0-1"]
[Variant "Fanoron-Tsivy"]
[Vela "Noirs"]
[FEN "BBBBBBBBB/BBBBBBBBB/BWBW1BWBW/WWWWWWWWW/WWWWWWWWW b 0 1"]
[Termination "Les Blancs abandonnent"]

1... f4-e3A 2. d1-d2 e3-f4W?? { -3.2 } 3. e1-d2 f3-e3W 0-1
```

- En-têtes : `Event`, `Site`, `Date` (AAAA.MM.JJ), `White`, `Black`, `Result` (`1-0`, `0-1`, `½-½`, `*`),
  `Variant` (`Fanoron-Tsivy` par défaut, `Fanoron-Dimy`, `Fanoron-Telo`), et selon le cas `Vela` (camp
  bénéficiaire), `FEN` (position de départ si ce n'est pas la position initiale), `TimeControl` (secondes+incrément),
  `EngineLevel`, `Termination`.
- Coups : notation des publications, cases reliées par des tirets, `A`/`W` après chaque prise (`d3-e3A-d4W`) ;
  pose du Fanoron-Telo : la case seule (`b2`). Numéros `1.`, `1...` quand les Noirs commencent.
- Optionnel : annotations `?!`, `?`, `??` et évaluations `{ +1.2 }` (point de vue des Blancs, en pions).
- À l'import, les tirets, numéros, commentaires `{…}`, variantes `(…)` et annotations sont ignorés ; une simple
  liste de coups (`d3e3A c3d3W`) est acceptée. Chaque coup est vérifié par le moteur.

## Elo du moteur

```sh
python3 tools/elo/calibrate.py --games 40 --gap2 24 --workers 5    # ~25 min sur fanorona-dev -> tools/gui/elo.json
```

L'échelle `STRENGTHS` de `tools/gui/server.py` va du tirage au sort pondéré après une recherche de profondeur 1
à la pleine force à 1 s par coup (NNUE net_v8). Tournoi entre réglages voisins et à deux crans, en réutilisant le
code de jeu du serveur (2 demi-coups d'ouverture au hasard, couleurs alternées), puis classements Bradley-Terry
(nulle = demi-point, une nulle virtuelle par paire), ancre : Débutant = 800. Calibration du 2026-10-04, avec le réseau net_v8 (744 parties, ajustement MM):

| Niveau | 1 Débutant | 2 Facile | 3 Intermédiaire | 4 Confirmé | 5 Expert | 6 Maître (1 s) |
|---|---|---|---|---|---|---|
| Elo | 800 | 1297 | 1681 | 1778 | 1850 | 2104 |

L'échelle n'est pas un Elo « officiel » (aucune population de joueurs classés de référence) : elle est interne et
ne vaut que dans ces conditions (Fanoron-Tsivy, net_v8, 1 s/coup, cette machine). Environ ±100 Elo d'incertitude
par écart entre réglages voisins (40 parties). Quelques petites inversions entre réglages voisins (ex. profondeur 3 fixe
sous le tirage pondéré à profondeur 3) : le curseur de l'interface n'utilise que les réglages d'Elo strictement croissant.
Ajustement : algorithme MM de Hunter (convergence garantie) ; une première version par montée de gradient à pas fixe
ne convergeait pas et gonflait les écarts du haut de l'échelle de plusieurs centaines d'Elo (calibrations du
2026-09-30 et du 2026-10-01 matin, corrigées). Elo du joueur :
E = 1/(1+10^((moteur − joueur)/400)), K = 40 pour les 20 premières parties classées puis 24, départ à 1000.

