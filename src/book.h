// Fanorona engine - livre d'ouvertures (Fanoron-Tsivy, règles standard)
#pragma once

#include <string>

#include "position.h"

namespace fanorona::Book {

// Charge un livre texte (tools/book/export_book.py) : une ligne par position,
//   <coups depuis la position initiale, ou "-">\t<coup> <poids> [<coup> <poids> ...]
// Chargement paresseux : les lignes sont indexées par leur suite de coups, les coups ne sont décodés qu'à la
// consultation (le serveur de la GUI relance un moteur par coup : charger doit rester rapide). Renvoie le
// nombre de positions chargées (0 si erreur ; le livre précédent est alors vidé).
size_t load(const std::string& path);
size_t size();

// Coup du livre (tirage pondéré) pour la position `pos` atteinte par `line` (coups depuis la position initiale,
// séparés par des espaces, notation canonique), ou MOVE_NONE : livre vide, suite inconnue, ou règles autres que
// le Fanoron-Tsivy standard (variante, vela, continuation obligatoire).
Move probe(const Position& pos, const std::string& line);

}  // namespace fanorona::Book
