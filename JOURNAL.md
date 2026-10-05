# Journal du projet Fanorona

Historique des étapes apportées au jeu, au moteur, au réseau et à l'interface, avec le résultat **mesuré** de
chacune (pas seulement « ce qui a été codé »). Le plus récent en haut. Une entrée par étape significative ; le
détail est dans le commit indiqué (`git show <commit>`).

Conventions : force mesurée par SPRT avec `tools/match.py` (bornes indiquées), vitesse par `bench` (nœuds/s,
fanorona-dev, i7-6700T, 1 thread), justesse par `make test`, `perft` et `tools/nnue/verify.py`.

---

## 2026-10-05

### NNUE — cycle gen11 (professeur net_v9 quantifié) : pas de gain, plateau · `b80f62a`
- gen11 : 40M positions sur c1 + c3 en 70 min (net_v9 quantifié, profondeur 6), chaîne automatique génération ->
  contrôle -> conversion compactée -> envoi -> entraînement GPU (n11d, 192 neurones, gen8 à gen11 = 155M positions,
  67 s/epoch, 7,9 Go sur la carte).
- Contre net_v9 : 51,0 % à profondeur 7 ; SPRT par paires arrêté à 1214 parties (W412 D397 L405, LLR −0,7,
  ≈ +1 Elo). net_v9 reste le réseau par défaut.
- Bilan : après net_v8 (données propres, +20) et net_v9 (volume, +6), la boucle « générer avec le meilleur réseau
  à profondeur 6, réentraîner » plafonne. Prochains leviers plausibles : étiquettes plus informatives (recherche
  plus profonde maintenant que les données sont propres, ou tables de finales), réglage de la recherche (SPSA).

### NNUE — réseau 256 quantifié : non retenu ; Elo recalibré en quantifié · `deaa063`
- n10x (256 neurones, gen8-10, 115M) : un poids de 36,9 imposait une échelle réduite sur une couche dense
  (corrigé dans b420ed9). Quantifié, il est presque aussi rapide que net_v9 ; 51,8 % à profondeur 7 mais
  **SPRT par paires H0 en 527 parties** contre net_v9 quantifié (W175 D170 L182). net_v9 reste le réseau par
  défaut ; 192 neurones est le bon compromis pour ces données.
- Correctif GUI (7f23dc1) : `server.py` prenait la ligne « quantification int16 disponible » pour une erreur de
  chargement (vu avec `tools/elo/calibrate.py`) ; seuls les vrais échecs (impossible d'ouvrir, invalide,
  tronqué) sont maintenant des erreurs.
- Elo des niveaux recalibré avec le moteur quantifié (fanorona-c1, 744 parties) : 800, 1281, 1711, 1795,
  1880, 2152 (le haut de l'échelle remonte avec le gain de la quantification).

### Moteur — inférence NNUE quantifiée int16 (≈ +9 Elo à réseau égal), activée par défaut · `8c068c5`
- Quantification construite au chargement des réseaux FNU2 : accumulateur int16 (exact, plus de dérive),
  couches denses int16 en AVX2 (`madd_epi16`), sortie en float. Échelle des activations QA choisie au chargement
  (511, 255 ou 127) selon une borne de pire cas garantissant l'absence de débordement ; sinon le moteur reste en
  float. Les poids des couches denses (jusqu'à ~14) excluent l'int8 sans réentraînement.
- Exactitude : `verify.py --quant` (référence numpy des mêmes calculs entiers) = moteur au centipion près sur
  2000 positions et 3 réseaux. Écart avec le float : ~1 cp en médiane.
- Vitesse (c1, `bench 8`) : 128 neurones +16 %, 192 (net_v9) +23 %, 256 +36 % : le gain croît avec la taille.
  net_v9 quantifié va aussi vite que net_v8 en float.
- SPRT net_v9 quantifié contre net_v9 float, 100 ms, par paires : **H1 en 651 parties** (W222 D224 L205,
  51,3 %). Option UCI `Quantized` (défaut true) ; signatures `bench 8` : net_v9 924 530, net_v8 891 093.
- Suite : réseau 256 entraîné sur gen8-10 (n10x) puis testé quantifié contre net_v9 quantifié.

### NNUE — net_v9 : 192 neurones sur 115M positions, nouveau réseau par défaut (≈ +6 Elo) · `6187325`
- n10d = accumulateur 192 (couches 16 -> 32, 4 buckets), entraîné sur GPU sur gen8 + gen9 + gen10 (115M
  positions, professeurs net_v6 et net_v8), 60 epochs ; ~6 % plus lent que net_v8.
- Contre net_v8 : 52,4 % à profondeur 7 (2000 parties), puis **SPRT par paires H1 en 1549 parties**
  (W551 D475 L523, 50,9 %, paires 0-43-661-68-1). Adopté sous le nom **net_v9** ; signature `bench 8` :
  1 063 465 nœuds.
- Interface et calibration : net_v9 par défaut ; Elo des niveaux recalibré sur fanorona-c1 (fanorona-dev est
  bridé à 1,2 cœur pour la température de pve2) : 800, 1259, 1650, 1739, 1801, 2027.
- Leçon : le volume de données propres (115M contre 95M ou 64M) a fait la différence que l'architecture seule
  (n8d, n9d) ne faisait pas.

### NNUE — cycle gen9/gen10 (professeur net_v8) et variantes de net_v8 : aucun gain · `0778f62`
- Données : gen9 = 30,8M positions (net_v8 professeur ; part de fanorona-dev écourtée, 4 lignes coupées
  retirées), gen10 = 20M (c1 + c3, 36 min). Format compacté ; indices du mode CUDA Graph passés en int32
  (`train.py`) pour tenir 115M positions dans les 8 Go de la carte.
- Contre net_v8, profondeur 7 (2000 parties) puis SPRT par paires à 100 ms (c1 + c3) :
  n8a (gen8, symétrie) 51,1 % / 50,5 % (1007 parties) ; n8d (gen8, 192) 51,1 % ; n8x (gen8, 256, val_loss
  0,00619) 51,6 % mais ~20 % plus lent ; n9 (gen8 + gen9, 128) 51,2 % / 50,3 % (1010) ; n9d (gen8 + gen9, 192)
  51,6 % / 50,4 % (1006). SPRT arrêtés sans verdict : écart de 1 à 2 Elo au plus. net_v8 reste le défaut.
- Leçon : après le saut des données propres, on retrouve les rendements décroissants ; les gains à profondeur
  égale (+8 à +11 Elo) ne passent pas à temps égal. Prochain levier : la quantification (rendre le 256 rapide).
- Infra : pve2 montait à 86 °C sous gensfen (fanorona-dev, 5 processus) ; VM 3180 limitée à `cpulimit 1.2`
  (≈ 69 °C ; repos ≈ 60-62 °C, marge thermique faible : refroidissement de pve2 à vérifier). Les gros calculs
  passent sur c1 et c3. Machine GPU : mise en veille désactivée, files d'entraînement lancées détachées de SSH
  (`wmic process call create`, journaux dans C:\fanorona\out).

### NNUE — net_v8 : données propres, nouveau réseau par défaut (≈ +20 Elo) · `c2e1091`
- gen8 générée avec le binaire corrigé (dérive de l'accumulateur) et net_v6 comme professeur : 40M positions sur
  fanorona-dev + c3 (78 min) et 24M sur c1 (58 min), profondeur 6. Qualité stable du début à la fin de chaque
  processus (accord score/résultat 0,018, contre 0,032 -> 0,050 dans gen7).
- Format de données compacté pour tenir dans la machine GPU : 90 entrées sur 12 octets (`packed`), conversion en
  flux depuis le texte, décompactage par batch sur la carte ; 64M positions = 770 Mo (5,8 Go avant).
- net_v8 = architecture de net_v6, 60 epochs sur GPU : val_loss 0,00669 (0,01215 pour net_v6 sur ses données).
  Contre net_v6 : 52,9 % à profondeur 7 (2000 parties), **SPRT par paires H1 en 293 parties** (W114 D82 L97,
  paires 0-2-125-16-0). Signature `bench 8` : 886 119 nœuds.
- Interface : net_v8 par défaut ; Elo des niveaux recalibré (744 parties) : 800, 1297, 1681, 1778, 1850, 2104.
  Le haut de l'échelle baisse sans que le moteur faiblisse : l'ancre (Débutant = 800) joue aussi avec le réseau
  et progresse avec lui.
- Généalogie de la page Labo mise à jour (net_v7, net_v7b, net_sw_d, net_g6a, net_v8) et courbes d'entraînement
  GPU copiées dans logs/.

### Moteur — BUG : dérive de l'accumulateur NNUE, données d'entraînement dégradées · `1a6930a`
- Piste : entraîner sur gen6 + gen7 (40M positions, net_v6 professeur) donnait de MOINS bons réseaux que gen6
  seul (12M). Mêmes paramètres, même professeur ; mais l'accord entre score et résultat des parties était bien
  pire dans gen7 (0,043 contre 0,029), uniformément sur toutes les machines.
- Cause : l'accumulateur incrémental (float32) n'était recalculé qu'au chargement du réseau ou sur `ucinewgame`.
  Les ajouts/retraits successifs de colonnes accumulent des erreurs d'arrondi : une même position passe de 595 à
  585 cp après 3 recherches de profondeur 11. `gensfen` n'envoie jamais `ucinewgame` : au fil d'un processus,
  l'accord score/résultat passe de 0,025 à 0,050 (gen7, processus d'une heure, 3,5M positions) et de 0,020 à
  0,039 (gen6, 923k positions par processus). Les processus plus longs de gen7 expliquent l'écart avec gen6.
- Correctif : recalcul complet de l'accumulateur toutes les 256 évaluations (src/nnue.cpp). Plus aucune dérive
  (595 cp après 9 recherches), même vitesse ; `verify.py` OK, tests OK ; signatures bench NNUE modifiées.
- Portée : **toutes les données gen1 à gen7 sont dégradées**, d'autant plus que les processus étaient longs ;
  les matchs l'étaient peu (`match.py` envoie `ucinewgame` à chaque partie) ; la GUI pas (un processus par coup).
  Les conclusions « le réseau plafonne » des cycles 4, 5 et du balayage GPU sont à revoir avec des données propres.

### Outils — SPRT par paires d'ouvertures (pentanomial) : ~7x moins de parties · `4e49352`
- Constat, sur les journaux de match : entre deux réseaux proches, les blancs gagnent ~54 % des parties et les
  noirs ~12 % ; 58 % des paires d'ouvertures (même ouverture, couleurs inversées) finissent 1-1, chaque moteur
  gagnant avec les blancs. Le SPRT traitait les parties comme indépendantes : variance surestimée ~8x
  (erreur-type 0,0090 au lieu de 0,0032), d'où des tests ~7x trop longs.
- `tools/sprt.py` + `match.py` : LLR sur les scores de paire (LL, LD, DD|WL, WD, WW), comme fishtest ; pas de
  décision avant 50 paires ; ancien mode avec `--trinomial`. Journal live : champ `pairs`.
- Rejoué sur les journaux : net_v6 contre net_v3 (H1 en 9823 parties) l'aurait été en ~1400 ; les SPRT arrêtés
  sans verdict ces derniers jours (symétrie, balayage, g6a) auraient tous conclu H0 vers 1800-2600 parties :
  les décisions prises restent valables.

### NNUE — balayage d'architectures sur GPU ; entraînement en CUDA Graph · `29fedba`
- `train.py --cuda-graph` : 8 pas d'entraînement enregistrés dans un graphe CUDA et rejoués d'un seul appel,
  mélange des indices sur la carte, copie des données libérée de la RAM. La carte n'était occupée qu'à 27-44 %
  (le Core 2 Quad ne la nourrissait pas) ; 7 s/epoch au lieu de 12-13 s, soit ~13× le processeur de fanorona-dev.
- Moteur : chemin AVX2 des couches denses généralisé (patron C++) aux formes 8/16/32 x 16/32 ; mêmes
  signatures bench qu'avant (net_v3 933 003, net_v6 863 974).
- 6 architectures entraînées chacune en ~7-15 min (gen3 + gen4, 60 epochs), contre net_v6 à profondeur fixe 7
  (2000 parties, ±1 %) — référence net_v6 réentraîné sur GPU : 49,6 % :
  accumulateur 256 + 16x32 : 50,9 % (≈1,05M nps) ; 256 + 8x32 : 50,9 % (1,17M) ; 256 + 8x16 : 49,8 % (1,24M) ;
  **192 + 16x32 : 51,2 % (1,29M)** ; 192 + 8x32 : 49,7 % (1,43M) ; 128 + 32x32 : 50,8 % (1,36M) ; net_v6 ≈ 1,38M.
- Constat : les réseaux plus grands ont une val_loss nettement meilleure (0,01177 contre 0,01215) mais pas une
  meilleure prédiction du résultat sur des positions jamais vues (gen5) : ils collent mieux aux étiquettes de
  ces données sans mieux généraliser. Les écarts en jeu sont de quelques Elo au plus.
- SPRT du meilleur compromis (192 + 16x32) contre net_v6 (100 ms, 0/+5, fanorona-dev + c3) : 49,7 % en 3009
  parties (W988 D1014 L1007, ≈ −2 Elo), arrêté. net_v6 reste le réseau par défaut. Conclusion : la limite
  n'est plus l'entraînement (désormais quasi gratuit) ni la taille du réseau, mais les données.

### Outils — entraînement des réseaux sur GPU · `454a79f`
- Machine Windows du réseau (andoniaina-desk, 10.10.10.200, GTX 1070 Ti) pilotée par SSH depuis fanorona-dev.
  `train.py --device` (auto : la carte si présente ; données copiées une fois dans la mémoire de la carte) et
  `--save-npz` (données converties en binaire : la machine n'a que 8 Go de RAM).
- 12 s par epoch en lots de 16 384 (≈ 7× le processeur de fanorona-dev) ; en lots de 1024, plus lent que le
  processeur (le Core 2 Quad limite le rythme). La recette de net_v6, refaite en 60 epochs : 13 min au lieu de
  ~37, val_loss 0,01210 contre 0,01215, même qualité sur des positions jamais vues.
- Contraintes : NumPy < 2.4 (le Core 2 Quad n'a pas SSE4.2), PyTorch compilé pour CUDA 12.6 (Pascal).

### NNUE — augmentation par symétrie : meilleure généralisation, pas de gain en partie · `454a79f`
- Les 4 symétries du plateau conservent le jeu (vérifié : mêmes coups légaux et même éval HCE sur 9000
  positions retournées, nouvel outil `tools/nnue/symmetry.py`).
- Constat : net_v6 évalue une même position très différemment selon son orientation (183 cp d'écart médian).
- `train.py --augment` : chaque position présentée dans une orientation au hasard. net_v7b (50 epochs) :
  écart divisé par deux (85 cp), meilleure prédiction du résultat sur des positions jamais vues dans les 4
  orientations, y compris l'originale.
- Mais SPRT contre net_v6 (100 ms, 0/+5, fanorona-dev + c3) : 50,2 % en 6862 parties (≈ +1 Elo), arrêté ;
  net_v7 (25 epochs) : 49,8 % en 3022. Les parties partent toutes de la même position : le bruit d'orientation
  coûte peu en jeu réel. net_v6 reste le réseau par défaut.

### Recherche — signatures de parité « type Sikidy » : sans valeur stratégique
- Hypothèse proposée : projeter une position sur 4 bits de parité (pièces de chaque camp, centre, mobilité,
  mod 2), comme les figures du Sikidy, et chercher un lien avec la valeur de la position.
- Mesure sur 200 000 positions réelles (information mutuelle avec le résultat de la partie, 1,55 bit au total) :
  la signature apporte 0,013 bit au-delà du matériel ; chaque grandeur prise en valeur brute apporte 2 à 100
  fois plus que sa parité (mobilité 0,059 contre 0,0006 ; voisinages 0,105 contre 0,0035). Le modulo 2 jette
  l'information qui compte (le nombre).
- Aucun invariant linéaire de parité non trivial n'existe : un coup simple impose aᵢ = aⱼ entre cases voisines,
  donc a constant ; et la parité du nombre total de pièces change à chaque capture impaire.
- Retenu de la piste : les symétries du plateau (entrée ci-dessus).

## 2026-10-03

### NNUE — architecture à couches empilées : net_v6, nouveau réseau par défaut · `4014a41`
- Constat de départ : 63 % des positions des données ont 8 pièces ou moins, 90 % en ont 12 ou moins ; et un réseau
  à une seule couche cachée ne représente presque que des valeurs de cases, pas leurs interactions.
- Nouveau format FNU2 : même accumulateur 90 -> hidden (mise à jour incrémentale inchangée), puis deux couches
  denses hidden -> 16 -> 32 -> 1, en 4 exemplaires selon le nombre de pièces (1-4, 5-7, 8-11, 12+). Moteur,
  `train.py` (`--l2 --l3 --buckets --lr-gamma`) et `verify.py` (2000/2000 à ±1 cp) ; l'ancien format reste lu.
- Vitesse : une boucle naïve rendait le moteur 2,5× plus lent ; en AVX2 avec 8 accumulateurs indépendants,
  accumulateur 256 : −20 %, accumulateur 128 : ≈ net_v3. Ne traiter que les neurones non nuls (72 %) : plus lent,
  abandonné.
- Contre net_v3 à profondeur fixe 7 (2000 parties) : 256 + buckets 52,9 % / 53,1 % (entraînement long) ;
  sans buckets 52,3 % ; 128 + buckets 51,2 % / 52,1 % (entraînement long). À 100 ms, le 256 ne fait que 50,5 %
  (4523 parties) : sa lenteur mange le gain.
- **net_v6** = 128 + buckets, entraînement long (25 epochs, taux × 0,9 par epoch), gen3 + gen4. SPRT contre
  net_v3, 100 ms, bornes 0/+5, 3 machines : **H1 acceptée en 9823 parties**, W3349 D3331 L3143 (51,05 %,
  ≈ +7 Elo). Signature `bench 8` : 863 974 nœuds.
- Déployé comme réseau par défaut de l'interface ; Elo des niveaux recalibré (744 parties) : 800, 1379, 1816,
  1996, 2054, 2306 (écarts avec la calibration précédente dans la marge de ±100 Elo).

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
