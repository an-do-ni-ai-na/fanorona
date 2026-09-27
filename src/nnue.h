// Fanorona engine - évaluation NNUE (réseau appris par auto-jeu, cf. tools/nnue/)
#pragma once

#include <string>

#include "position.h"

namespace fanorona::NNUE {

// Charge un fichier de poids exporté par tools/nnue/train.py (--export, format "FNUE").
// Renvoie false (et laisse l'état précédent inchangé) si le fichier est absent/invalide.
bool load(const std::string& path);

// Vrai si un réseau valide est chargé ET que l'option UCI UseNNUE est activée.
bool enabled();
void set_enabled(bool on);

// Évaluation NNUE, à utiliser à la place de evaluate() quand enabled() est vrai (voir
// evaluate.cpp). Recalcule tout depuis les bitboards à chaque appel : pas encore incrémental
// (l'accumulateur incrémental mis à jour dans Position::do_move est un travail séparé, cf.
// feuille de route CLAUDE.md — celui-ci n'affecte que la vitesse, pas la justesse).
Value evaluate(const Position& pos);

}  // namespace fanorona::NNUE
