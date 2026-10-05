// Fanorona engine - évaluation NNUE (réseau appris par auto-jeu, cf. tools/nnue/)
#pragma once

#include <string>

#include "position.h"

namespace fanorona::NNUE {

// Charge un fichier de poids exporté par tools/nnue/train.py (--export, formats "FNUE" et "FNU2").
// Renvoie false (et laisse l'état précédent inchangé) si le fichier est absent/invalide.
bool load(const std::string& path);

// Vrai si un réseau valide est chargé ET que l'option UCI UseNNUE est activée.
bool enabled();
void set_enabled(bool on);

// Inférence quantifiée int16 (option UCI Quantized) pour les réseaux FNU2 : poids quantifiés au chargement si
// le réseau s'y prête (sinon le moteur reste en float). quantized() = actif pour le réseau chargé.
void set_quantized(bool on);
bool quantized();

// Invalide l'accumulateur mis en cache (voir evaluate() ci-dessous). Pas obligatoire pour la
// justesse (le cache se corrige tout seul par diff quelle que soit sa fraîcheur), mais appelé
// depuis "ucinewgame" par hygiène, comme Search::clear().
void new_game();

// Évaluation NNUE, à utiliser à la place de evaluate() quand enabled() est vrai (voir
// evaluate.cpp). Maintient un accumulateur incrémental (dernier calculé, mis à jour par diff
// XOR des bitboards à chaque appel plutôt que recalculé — voir le commentaire détaillé dans
// nnue.cpp) : pas de paramètre supplémentaire, pas de changement à Position ni au copy-make.
Value evaluate(const Position& pos);

}  // namespace fanorona::NNUE
