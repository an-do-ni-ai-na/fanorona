# Charte graphique — interface Fanorona

Principe : **sobre, chaleureuse, lisible**. Le plateau est le seul objet « matière » (bois ou pierre) ; tout le reste
est neutre et s'efface. Une seule couleur d'accent, utilisée avec parcimonie. Aucune image : textures, pièces et
icônes sont générées en SVG. Tous les réglages vivent dans les variables CSS de `index.html` (`:root`), jamais en dur.

## Typographie

| Rôle | Police | Usage |
|---|---|---|
| Marque et titres forts | **Fraunces** 600 | logo « Fanorona », titre de la boîte de dialogue, score final (`1-0`) |
| Interface | **Inter** 400 / 500 / 600 | tout le texte courant, boutons, étiquettes |
| Données | **JetBrains Mono** 500 / 600 | notation des coups, pendules, chiffres du moteur (chiffres tabulaires) |

Repli sur les polices système si Google Fonts est injoignable. Étiquettes de section : 11,5 px, capitales, interlettrage
0,07 em, couleur `--muted`.

## Couleurs

Accent : **terre cuite** — rappel de la terre rouge malgache. Il marque l'action principale, le joueur au trait,
l'interrupteur actif, la progression. Jamais pour du texte courant ni pour des données.

| Rôle | Papier (clair) | Ardoise (sombre) | Veille (nuit) |
|---|---|---|---|
| Fond `--bg` | `#f2efe9` | `#141311` | `#0a0807` |
| Surface `--surface` | `#fdfcfa` | `#1e1c19` | `#120f0c` |
| Surface 2 (survol, zébrures) | `#f5f2ec` | `#262420` | `#19150f` |
| Bordure | `#e4ded4` | `#322f2a` | `#262019` |
| Texte / fort / discret | `#4a443b` / `#1f1b16` / `#8b8377` | `#c7c0b4` / `#f0ebe3` / `#8a8378` | `#9c8b74` / `#c4b08e` / `#66594a` |
| Accent | `#a84f28` | `#d2743f` | `#b0703a` (ambre) |
| Information (coup courant) | `#3e6a8a` | `#7fa9c9` | `#a58458` |
| Pendule active | encre sur papier inversée | papier sur encre inversée | brun chaud `#3a2c1c` |

Annotations d'analyse (`?!` imprécision, `?` erreur, `??` gaffe) — une palette par thème, **validée** (écart
perceptuel, daltonisme, contraste) avec l'outil de validation dataviz ; le symbole accompagne toujours la couleur :

| | Papier | Ardoise | Veille |
|---|---|---|---|
| `?!` | `#2f80d0` | `#3d8fd9` | `#5a90c8` |
| `?` | `#b87800` | `#c4850c` | `#bd8726` |
| `??` | `#d0302f` | `#e0454a` | `#d4474c` |

## Mode nuit — « Veille »

Pensé pour jouer dans le noir, pas seulement « sombre » : tons ambrés, aucun blanc pur ni bleu vif dans
l'interface, contraste réduit ; le plateau est assombri et réchauffé par un filtre (`brightness .6, sepia .35`),
les reflets des pièces sont éteints. Choix explicite dans le menu Apparence, ou automatique de 22 h à 7 h
(option « Veille automatique »). « Auto » suit le système entre Papier et Ardoise.

## Plateaux

| | Bois | Granite |
|---|---|---|
| Matière | dégradé miel `#e6c48e → #c7985a`, veines (bruit étiré) | gris `#a7a39c → #85817a`, mouchetures sombres et claires, marbrure lente |
| Lignes | brûlées `#58391b`, léger reflet | gravées `#2c2926` avec reflet clair décalé (sillon) |
| Pièces | ivoire et ébène, anneau intérieur tourné | galets : quartz et basalte très sombre (`#3f3d3b → #060606`), grain fin, sans anneau |

Commun aux deux : points forts (diagonales) marqués plus gros que les points faibles. Approche/retrait :
orange `#c2551f` / bleu `#2f6fb0`, toujours doublés du libellé.

## Marques de déplacement (suivent le plateau, pas le thème)

Variables `--hl-*`, définies dans le bloc de chaque plateau : dernier coup (`--hl-last`), sélection, cibles et cases
déjà parcourues (`--hl-sel`), tracé de la chaîne en cours (`--hl-path`), meilleur coup / indice en pointillés
(`--hl-best`), ombre portée des pièces (`--hl-shadow`) et ombre de la pièce glissée (`--hl-drag`).

| | Bois | Granite |
|---|---|---|
| Dernier coup | or translucide | jaune chaud translucide |
| Sélection, cibles, tracé | sarcelle vive `rgb(20 118 98)` | terre cuite vive `rgb(196 80 30)` |
| Meilleur coup, indice | bleu profond | bleu profond |
| Ombres des pièces | brun chaud | noir |

Les teintes sont volontairement vives et de luminosité moyenne : elles restent lisibles dans les trois thèmes,
y compris en Veille où le filtre assombrit tout le plateau (une marque foncée y disparaîtrait dans les lignes).

## Structure et navigation

En-tête collant : marque, onglets (l'actif est souligné par l'accent), puce de profil ouvrant un menu (profil,
thème, plateau, langue, veille, sons). Sur mobile (< 800 px), les onglets passent dans une barre fixe en bas,
avec icônes. Pages : Accueil (cartes), Jouer, Puzzles et Apprendre, Parties (historique).
Jouer, sur ordinateur (> 800 px) : **le plateau d'abord** — il prend toute la largeur à gauche (≈ 1000 px en
1440 × 900, contre 740 avec trois colonnes) ; à droite une colonne de 300 à 340 px (joueurs, pendules, coups,
navigation, actions) qui a exactement la hauteur du plateau (la liste des coups remplit l'espace) ; les outils
(carte de partie, analyse en continu, ouvertures, moteur) forment un bandeau de cartes pleine largeur sous le
plateau, l'analyse d'après partie sur toute la largeur. Puzzles, Apprendre et révision : même plateau, la consigne
dans la colonne de droite (la liste des coups, inutile ici, est masquée). Sur mobile, consigne sous le plateau. Le joueur au trait est surligné (fond accent léger).
Téléphone en paysage (hauteur ≤ 520 px, PWA ou navigateur) : sur les pages à plateau, en-tête et barre d onglets
masqués, plateau sur toute la hauteur, colonne étroite à droite (pendules, joueurs, coups, contrôles en icônes, ou
la consigne en puzzles / tutoriel / révision) ; le portrait rend la navigation.
Texte secondaire `--muted` relevé (contraste ≥ 4,5:1 sur Papier), corps de texte 15 px, boutons ≥ 40 px de haut.

## Formes et espacements

Rayons 10 px (cartes) et 6 px (contrôles) ; bordures 1 px plutôt que des ombres marquées ; ombres légères
uniquement pour la profondeur (plateau, menus). Grille de 4 px. Focus clavier visible (contour accent 2 px).
Transitions courtes (0,2 s), aucune animation décorative. Retours de jeu (2026-10-08) : les pièces glissent (0,22 s), une pièce capturée
rétrécit en s'estompant (0,25 s) ; pendant la réflexion du moteur, le point du joueur au trait pulse en plus de la
barre de progression ; en fin de partie, une carte posée sur le plateau (panneau montant du bas sur téléphone)
donne le résultat du point de vue du joueur (« Victoire ! » en accent, « Défaite », « Partie nulle ») et trois
actions : Revanche (principale), Analyser, Voir le plateau. Vibration courte (capture, fin) sur appareil tactile,
liée au réglage des sons.
