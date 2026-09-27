// Tests des règles, de la génération des coups et de la recherche.
#include <iostream>
#include <random>
#include <string>

#include "bitboard.h"
#include "movegen.h"
#include "position.h"
#include "search.h"
#include "tt.h"

using namespace fanorona;

static int failures = 0;
#define CHECK(cond)                                                                   \
    do {                                                                              \
        if (!(cond)) {                                                                \
            std::cerr << __FILE__ << ":" << __LINE__ << ": CHECK failed: " #cond "\n"; \
            ++failures;                                                               \
        }                                                                             \
    } while (0)

static Position from_fen(const std::string& fen) {
    Position p;
    bool ok = p.set(fen);
    CHECK(ok);
    return p;
}

static int count_moves(const Position& p) {
    MoveList l;
    generate_moves(p, l);
    return l.size;
}

static Bitboard bb(std::initializer_list<const char*> squares) {
    Bitboard b = 0;
    for (auto s : squares) b |= square_bb(string_to_square(s));
    return b;
}

// Rotation de 180° + échange des couleurs : position équivalente.
static Position mirrored(const Position& p) {
    Position m{};
    for (int c = 0; c < COLOR_NB; ++c)
        for (Bitboard b = p.byColor[c]; b;) m.byColor[1 - c] |= square_bb(SQUARE_NB - 1 - pop_lsb(b));
    m.sideToMove = ~p.sideToMove;
    m.rule50 = p.rule50;
    m.gamePly = p.gamePly;
    m.key = m.compute_key();
    return m;
}

static void test_perft_startpos() {
    Position p = from_fen(START_FEN);
    const uint64_t expected[] = {1, 5, 39, 724, 18026, 431830};
    for (int d = 1; d <= 5; ++d) CHECK(perft(p, d) == expected[d]);
}

static void test_opening_moves() {
    Position p = from_fen(START_FEN);
    CHECK(parse_move(p, "d3e3A") != MOVE_NONE);
    CHECK(parse_move(p, "d3e3W") != MOVE_NONE);
    CHECK(parse_move(p, "e2e3A") != MOVE_NONE);
    CHECK(parse_move(p, "d3e3") == MOVE_NONE);  // ambigu (approche ou retrait)
    CHECK(parse_move(p, "e2e3") == parse_move(p, "e2e3A"));
    Move m = parse_move(p, "d3e3A");
    CHECK(move_captured(m) == bb({"f3"}));
    m = parse_move(p, "d3e3W");
    CHECK(move_captured(m) == bb({"c3"}));
    m = parse_move(p, "e2e3A");
    CHECK(move_captured(m) == bb({"e4", "e5"}));  // la ligne entière est capturée
}

static void test_approach_and_withdrawal() {
    // c3 noir, d3 blanc, f3 noir : d3-e3 peut capturer par approche (f3) OU retrait (c3).
    Position p = from_fen("9/9/2BW1B3/9/9 w");
    CHECK(count_moves(p) == 2);
    CHECK(move_captured(parse_move(p, "d3e3A")) == bb({"f3"}));
    CHECK(move_captured(parse_move(p, "d3e3W")) == bb({"c3"}));

    // Capture d'une ligne contiguë, arrêtée par une case vide.
    p = from_fen("9/9/1B1W1BB2/9/9 w");
    CHECK(move_captured(parse_move(p, "d3e3A")) == bb({"f3", "g3"}));
    CHECK(count_moves(p) == 2);  // d3e3A et d3c3A
}

static void test_capture_chain() {
    // c1 blanc, e1 noir, d3 noir : c1-d1 (approche e1) puis d1-d2 (approche d3).
    Position p = from_fen("9/9/3B5/9/2W1B4 w");
    CHECK(count_moves(p) == 2);  // on peut s'arrêter après la première capture
    Move m = parse_move(p, "c1d1Ad2A");
    CHECK(m != MOVE_NONE && move_captured(m) == bb({"e1", "d3"}) && move_to(m) == string_to_square("d2"));

    Rules::mandatoryContinuation = true;
    CHECK(count_moves(p) == 1);
    Rules::mandatoryContinuation = false;
}

static void test_same_direction_forbidden() {
    // b1 blanc recule vers c1 en capturant a1 ; continuer vers d1 (même
    // direction) capturerait e1 par approche, mais c'est interdit.
    Position p = from_fen("9/9/9/9/BW2B4 w");
    CHECK(count_moves(p) == 1);
    CHECK(parse_move(p, "b1c1W") != MOVE_NONE);
}

static void test_visited_forbidden() {
    // b2 blanc : b2-c2 (retrait, capture a2) puis c2-c1 (approche impossible),
    // on vérifie que la pièce ne peut jamais revenir sur b2.
    Position p = from_fen("9/9/9/B1W1B4/9 w");
    for (const auto& dm : generate_detailed(p)) {
        std::string s = dm.notation;
        CHECK(s.find("c2", 2) == std::string::npos);  // c2 est la case de départ
    }
}

static void test_paika_only_without_capture() {
    Position p = from_fen("9/9/9/9/W7B w");
    CHECK(count_moves(p) == 3);  // a1 -> a2, b1, b2
    MoveList l;
    generate_moves(p, l);
    for (Move m : l) CHECK(!is_capture(m));

    // Dès qu'une capture existe, les déplacements simples sont interdits.
    p = from_fen("9/9/9/9/W1B6 w");
    generate_moves(p, l);
    CHECK(l.size == 1 && is_capture(l.moves[0]));
}

static void test_diagonals_only_on_strong_points() {
    Position p = from_fen("9/9/9/9/1W7 w");  // b1 : point faible
    CHECK(count_moves(p) == 3);              // a1, c1, b2 : pas de diagonale
    p = from_fen("9/9/4W4/9/9 w");           // e3 : point fort, 8 voisins
    CHECK(count_moves(p) == 8);
}

static void test_symmetry_and_keys() {
    std::mt19937_64 rng(42);
    for (int game = 0; game < 30; ++game) {
        Position p = from_fen(START_FEN);
        for (int ply = 0; ply < 40; ++ply) {
            CHECK(p.key == p.compute_key());
            Position q = from_fen(p.fen());
            CHECK(q.key == p.key && q.fen() == p.fen());
            if (ply % 10 == 5) CHECK(perft(p, 3) == perft(mirrored(p), 3));
            MoveList l;
            generate_moves(p, l);
            if (!l.size) break;
            for (Move m : l) CHECK(parse_move(p, move_to_string(p, m)) == m);
            p.do_move(l.moves[rng() % l.size]);
        }
    }
}

static void test_search() {
    // Gain immédiat : a1-b1 capture la dernière pièce noire.
    Position p = from_fen("9/9/9/9/W1B6 w");
    SearchLimits l;
    l.depth = 6;
    auto r = Search::think(p, {p.key}, l, false);
    CHECK(r.bestMove == parse_move(p, "a1b1A"));
    CHECK(r.score == VALUE_MATE - 1);

    // Plus de pièces : défaite, aucun coup.
    p = from_fen("9/9/9/9/W8 b");
    r = Search::think(p, {p.key}, l, false);
    CHECK(r.bestMove == MOVE_NONE);

    // La recherche doit préférer la grosse capture (2 pièces) à la petite.
    p = from_fen("9/9/1B1W1BB2/9/9 w");
    l.depth = 4;
    r = Search::think(p, {p.key}, l, false);
    CHECK(move_captured(r.bestMove) == bb({"f3", "g3"}));

    // Recherche depuis la position initiale : coup légal renvoyé.
    p = from_fen(START_FEN);
    l.depth = 6;
    r = Search::think(p, {p.key}, l, false);
    MoveList ml;
    generate_moves(p, ml);
    CHECK(ml.contains(r.bestMove));
}

int main() {
    init_bitboards();
    Zobrist::init();
    Search::init();
    TT.resize(16);

    test_perft_startpos();
    test_opening_moves();
    test_approach_and_withdrawal();
    test_capture_chain();
    test_same_direction_forbidden();
    test_visited_forbidden();
    test_paika_only_without_capture();
    test_diagonals_only_on_strong_points();
    test_symmetry_and_keys();
    test_search();

    if (failures) {
        std::cerr << failures << " test(s) en échec\n";
        return 1;
    }
    std::cout << "Tous les tests passent.\n";
    return 0;
}
