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
| Pièces | ivoire et ébène, anneau intérieur tourné | galets : quartz et basalte, grain fin, sans anneau |

Commun aux deux : ombre portée douce sous chaque pièce, points forts (diagonales) marqués plus gros que les
points faibles, dernier coup en or translucide, sélection et cibles en sarcelle foncée, meilleur coup du moteur en
pointillés bleus. Approche/retrait : orange `#c2551f` / bleu `#2f6fb0`, toujours doublés du libellé.

## Formes et espacements

Rayons 10 px (cartes) et 6 px (contrôles) ; bordures 1 px plutôt que des ombres marquées ; ombres légères
uniquement pour la profondeur (plateau, menus). Grille de 4 px. Focus clavier visible (contour accent 2 px).
Transitions courtes (0,2 s), aucune animation décorative.
