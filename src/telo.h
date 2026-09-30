// Fanorona engine - Fanoron-Telo (3 x 3)
//
// Jeu d'alignement, sans capture (source : Ludii, d'après l'ethnographie de l'Imerina, 1951) :
//   - plateau 3 x 3 avec les deux diagonales (points forts : coins et centre, comme au Fanoron-Tsivy) ;
//   - 3 pions par camp. Phase de pose : chacun pose à son tour un pion sur un point libre (les Blancs
//     commencent) ; phase de déplacement ensuite : un pion vers un point voisin libre, le long d'une ligne ;
//   - aligner ses 3 pions sur une ligne (rangée, colonne, diagonale) gagne, y compris pendant la pose ;
//     un camp sans coup possible a perdu ; nulle par triple répétition ou après Rules::noCaptureLimit
//     demi-coups de déplacement.
// Notation : pose = la case ("b2"), déplacement = départ + arrivée ("a1b2").
// Le jeu est entièrement résolu au démarrage (analyse rétrograde, < 2^19 états) : `go` sans profondeur joue
// parfaitement ; `go depth N` fait une recherche limitée (sert aux niveaux faibles).
#pragma once

#include <string>
#include <vector>

#include "types.h"

namespace fanorona::Telo {

struct State {
    int w = 0, b = 0;         // pions (bits 0..8, case = y * 3 + x, a1 = 0)
    Color stm = WHITE;
    int quiet = 0;            // demi-coups de déplacement (règle de nulle)
    int ply = 0;
    Key key() const { return Key(w) | Key(b) << 9 | Key(stm) << 18; }
};

struct TeloMove {
    std::string notation;
    State child;
};

void init();  // résolution complète (idempotent)
bool set_fen(State& st, const std::string& fen);
std::string fen(const State& st);
std::string pretty(const State& st);
std::vector<TeloMove> moves(const State& st);
// "ongoing", "white wins", "black wins", "draw (threefold repetition)", "draw (move limit)"
std::string status(const State& st, const std::vector<Key>& history);
// Valeur exacte pour le camp au trait : N > 0 gagne en N demi-coups, -(N + 1) perd en N demi-coups, 0 nulle.
int solved(const State& st);
// Recherche : imprime "info ..." puis "bestmove ...". depth <= 0 : jeu parfait (table de résolution).
void go(const State& st, int depth);
uint64_t perft(const State& st, int depth);

}  // namespace fanorona::Telo
