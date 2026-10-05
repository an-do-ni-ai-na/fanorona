#include "uci.h"

#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "bitboard.h"
#include "evaluate.h"
#include "movegen.h"
#include "nnue.h"
#include "search.h"
#include "telo.h"
#include "tt.h"

namespace fanorona::UCI {

namespace {

constexpr const char* ENGINE_NAME = "Fanorona-Engine 0.1";

struct Game {
    Position pos;
    std::vector<Key> history;  // clés depuis le début de la partie (dernière = position courante)
    Telo::State telo;          // position si la variante est le Fanoron-Telo (pos inutilisée)

    void reset(const Position& p) {
        pos = p;
        history.assign(1, p.key);
    }
    void play(Move m) {
        pos.do_move(m);
        history.push_back(pos.key);
    }
};

int multiPV = 1;  // option UCI MultiPV (lignes d'analyse)

bool is_telo() { return Rules::variant == Variant::Telo; }

// Résultat de la partie du point de vue des règles.
std::string game_status(const Game& g) {
    if (is_telo()) return Telo::status(g.telo, g.history);
    MoveList list;
    generate_moves(g.pos, list);
    if (list.size == 0) return g.pos.sideToMove == WHITE ? "black wins" : "white wins";
    if (g.pos.rule50 >= Rules::noCaptureLimit) return "draw (no-capture limit)";
    int reps = 0;
    for (int i = int(g.history.size()) - 1, k = 0; i >= 0 && k <= g.pos.rule50; --i, ++k)
        if (g.history[i] == g.pos.key) ++reps;
    if (reps >= 3) return "draw (threefold repetition)";
    return "ongoing";
}

void cmd_position_telo(Game& g, std::istringstream& is) {
    std::string token, fen;
    is >> token;
    Telo::State st;
    if (token == "startpos") is >> token;
    else if (token == "fen") {
        while (is >> token && token != "moves") fen += token + " ";
        if (!Telo::set_fen(st, fen)) {
            std::cout << "info string invalid fen" << std::endl;
            return;
        }
    } else
        return;
    g.telo = st;
    g.history.assign(1, st.key());
    while (is >> token) {
        bool ok = false;
        for (const auto& m : Telo::moves(g.telo))
            if (m.notation == token) {
                g.telo = m.child, ok = true;
                break;
            }
        if (!ok) {
            std::cout << "info string illegal move " << token << std::endl;
            break;
        }
        g.history.push_back(g.telo.key());
    }
}

void cmd_position(Game& g, std::istringstream& is) {
    if (is_telo()) return cmd_position_telo(g, is);
    std::string token, fen;
    is >> token;
    Position p;
    if (token == "startpos") {
        p.set(start_fen());
        is >> token;  // "moves" éventuel
    } else if (token == "fen") {
        while (is >> token && token != "moves") fen += token + " ";
        if (!p.set(fen)) {
            std::cout << "info string invalid fen" << std::endl;
            return;
        }
    } else
        return;

    g.reset(p);
    while (is >> token) {
        Move m = parse_move(g.pos, token);
        if (m == MOVE_NONE) {
            std::cout << "info string illegal move " << token << std::endl;
            break;
        }
        g.play(m);
    }
}

SearchLimits parse_limits(std::istringstream& is) {
    SearchLimits l;
    std::string token;
    while (is >> token) {
        if (token == "depth") is >> l.depth;
        else if (token == "movetime") is >> l.movetime;
        else if (token == "wtime") is >> l.time[WHITE];
        else if (token == "btime") is >> l.time[BLACK];
        else if (token == "winc") is >> l.inc[WHITE];
        else if (token == "binc") is >> l.inc[BLACK];
        else if (token == "movestogo") is >> l.movestogo;
        else if (token == "nodes") is >> l.nodes;
        else if (token == "infinite") l.infinite = true;
    }
    return l;
}

void cmd_perft(const Position& pos, int depth) {
    auto t0 = std::chrono::steady_clock::now();
    uint64_t total = 0;
    MoveList list;
    generate_moves(pos, list);
    for (Move m : list) {
        Position p = pos;
        p.do_move(m);
        uint64_t n = depth > 1 ? perft(p, depth - 1) : 1;
        total += n;
        std::cout << move_to_string(pos, m) << ": " << n << '\n';
    }
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
    std::cout << "\nNodes searched: " << total << "\nTime: " << ms << " ms\nNPS: " << total * 1000 / std::max<int64_t>(1, ms)
              << std::endl;
}

// Positions de bench : parties aléatoires déterministes depuis la position initiale.
std::vector<Position> bench_positions() {
    std::vector<Position> v;
    std::mt19937_64 rng(20260927);
    Position start;
    start.set(start_fen());
    while (v.size() < 16) {
        Position p = start;
        for (int ply = 0; ply < 60 && v.size() < 16; ++ply) {
            MoveList list;
            generate_moves(p, list);
            if (list.size == 0) break;
            p.do_move(list.moves[rng() % list.size]);
            if (ply % 7 == 3) v.push_back(p);
        }
    }
    v.insert(v.begin(), start);
    return v;
}

void cmd_bench(int depth) {
    uint64_t nodes = 0;
    int64_t ms = 0;
    Search::clear();
    for (const Position& p : bench_positions()) {
        SearchLimits l;
        l.depth = depth;
        auto r = Search::think(p, {p.key}, l, false);
        std::cout << p.fen() << "  bestmove " << move_to_string(p, r.bestMove) << "  nodes " << r.nodes << std::endl;
        nodes += r.nodes;
        ms += r.timeMs;
    }
    std::cout << "\n===========================\nTotal time (ms) : " << ms << "\nNodes searched  : " << nodes
              << "\nNodes/second    : " << nodes * 1000 / std::max<int64_t>(1, ms) << std::endl;
}

// Partie interactive contre le moteur dans le terminal.
void cmd_play(std::istringstream& is) {
    std::string side = "w";
    int64_t movetime = 2000;
    is >> side >> movetime;
    Color human = (side == "b" || side == "black") ? BLACK : WHITE;

    Game g;
    Position start;
    start.set(start_fen());
    g.reset(start);
    Search::clear();

    std::cout << "Vous jouez les " << (human == WHITE ? "blancs (W)" : "noirs (B)")
              << ". Entrez un coup (ex. d3e3A), 'moves' pour la liste, 'quit' pour abandonner.\n";
    while (true) {
        std::cout << '\n' << g.pos.pretty();
        std::string status = game_status(g);
        if (status != "ongoing") {
            std::cout << "Fin de partie : " << status << std::endl;
            return;
        }
        if (g.pos.sideToMove == human) {
            std::cout << "Votre coup> " << std::flush;
            std::string line;
            if (!std::getline(std::cin, line) || line == "quit") return;
            if (line == "moves") {
                for (const auto& dm : generate_detailed(g.pos)) std::cout << dm.notation << ' ';
                std::cout << std::endl;
                continue;
            }
            Move m = parse_move(g.pos, line);
            if (m == MOVE_NONE) {
                std::cout << "Coup illégal ou ambigu." << std::endl;
                continue;
            }
            g.play(m);
        } else {
            SearchLimits l;
            l.movetime = movetime;
            auto r = Search::think(g.pos, g.history, l, false);
            std::cout << "Le moteur joue " << move_to_string(g.pos, r.bestMove) << "  (profondeur " << r.depth
                      << ", score " << r.score << ")" << std::endl;
            g.play(r.bestMove);
        }
    }
}

// Génère des données d'entraînement NNUE par auto-jeu à profondeur fixe (façon "gensfen") :
// quelques coups aléatoires en ouverture (diversité), puis coups décidés par Search::think.
// Chaque position visitée après l'ouverture est enregistrée avec le score de la recherche et,
// une fois la partie terminée, le résultat final vu du camp au trait à cette position-là
// (1.0 victoire, 0.5 nulle, 0.0 défaite). Format texte, une ligne par position :
//   <fen>|<score_cp>|<wdl>
// Les parties tronquées par le garde-fou de longueur (pas de fin de partie franche) sont
// jetées : leur résultat ne serait pas fiable comme cible d'entraînement.
void cmd_gensfen(std::istringstream& is) {
    uint64_t targetPositions = 100000;
    int depth = 6;
    int openingPlies = 8;
    std::string outPath = "gensfen.txt";
    std::string tok;
    while (is >> tok) {
        if (tok == "count") is >> targetPositions;
        else if (tok == "depth") is >> depth;
        else if (tok == "opening-plies") is >> openingPlies;
        else if (tok == "out") is >> outPath;
    }

    std::ofstream out(outPath, std::ios::app);
    if (!out) {
        std::cout << "info string gensfen: impossible d'ouvrir " << outPath << std::endl;
        return;
    }

    struct Sample {
        std::string fen;
        Value score;
        Color stm;
    };

    std::mt19937_64 rng(std::random_device{}());
    Search::clear();
    uint64_t written = 0, games = 0;
    auto t0 = std::chrono::steady_clock::now();

    while (written < targetPositions) {
        Game g;
        Position start;
        start.set(start_fen());
        g.reset(start);

        std::vector<Sample> samples;
        std::string result = "ongoing";
        for (int ply = 0; ply <= 400; ++ply) {
            result = game_status(g);
            if (result != "ongoing") break;

            if (ply < openingPlies) {
                MoveList list;
                generate_moves(g.pos, list);
                g.play(list.moves[rng() % list.size]);
            } else {
                SearchLimits l;
                l.depth = depth;
                auto r = Search::think(g.pos, g.history, l, false);
                samples.push_back({g.pos.fen(), r.score, g.pos.sideToMove});
                g.play(r.bestMove);
            }
        }

        bool finished = result == "white wins" || result == "black wins" || result.rfind("draw", 0) == 0;
        if (finished) {
            double whiteResult = result == "white wins" ? 1.0 : result == "black wins" ? 0.0 : 0.5;
            for (const auto& s : samples) {
                double wdl = s.stm == WHITE ? whiteResult : 1.0 - whiteResult;
                out << s.fen << '|' << s.score << '|' << wdl << '\n';
            }
            written += samples.size();
        }
        ++games;
        if (games % 20 == 0) {
            out.flush();
            auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
            std::cout << "info string gensfen " << written << "/" << targetPositions << " positions, " << games
                      << " parties, " << (ms > 0 ? written * 1000 / ms : 0) << " pos/s" << std::endl;
        }
    }
    std::cout << "info string gensfen terminé : " << written << " positions -> " << outPath << std::endl;
}

void print_help() {
    std::cout << ENGINE_NAME << " - commandes :\n"
              << "  uci | isready | ucinewgame | quit\n"
              << "  position startpos|fen <fen> [moves m1 m2 ...]\n"
              << "  go [depth N] [movetime ms] [wtime ms btime ms winc ms binc ms movestogo N] [nodes N] [infinite]\n"
              << "  stop\n"
              << "  setoption name <Hash|MandatoryContinuation|NoCaptureLimit|UseNNUE|EvalFile|Quantized> value <v>\n"
              << "  setoption name Variant value tsivy|dimy|telo   (9x5, 5x5, 3x3)\n"
              << "  setoption name Vela value none|white|black     (partie vela : camp bénéficiaire)\n"
              << "  d            affiche la position\n"
              << "  moves        liste les coups légaux\n"
              << "  eval         évaluation statique\n"
              << "  status       état de la partie (ongoing / white wins / black wins / draw)\n"
              << "  perft N      compte les feuilles à la profondeur N (par coup)\n"
              << "  bench [N]    test de performance (profondeur N, défaut 8)\n"
              << "  play [w|b] [ms]   jouer contre le moteur\n"
              << "  gensfen [count N] [depth N] [opening-plies N] [out fichier]   génère des données NNUE par auto-jeu\n"
              << "Notation : cases a1..i5 ; chaque étape de capture est suivie de A (approche) ou W (retrait).\n"
              << std::endl;
}

}  // namespace

void loop(int argc, char* argv[]) {
    Game game;
    Position start;
    start.set(start_fen());
    game.reset(start);
    std::thread searchThread;

    auto join = [&]() {
        if (searchThread.joinable()) searchThread.join();
    };

    std::string cmd, token;
    for (int i = 1; i < argc; ++i) cmd += std::string(argv[i]) + " ";

    do {
        if (argc == 1 && !std::getline(std::cin, cmd)) cmd = "quit";

        std::istringstream is(cmd);
        token.clear();
        is >> std::skipws >> token;

        if (token == "quit" || token == "stop") {
            Search::stopSignal = true;
            join();
        } else if (token == "uci") {
            std::cout << "id name " << ENGINE_NAME << "\nid author fanorona contributors\n"
                      << "option name Hash type spin default 64 min 1 max 16384\n"
                      << "option name MandatoryContinuation type check default false\n"
                      << "option name NoCaptureLimit type spin default 100 min 10 max 10000\n"
                      << "option name UseNNUE type check default false\n"
                      << "option name EvalFile type string default <empty>\n"
                      << "option name Quantized type check default true\n"
                      << "option name MultiPV type spin default 1 min 1 max 8\n"
                      << "option name Variant type combo default tsivy var tsivy var dimy var telo\n"
                      << "option name Vela type combo default none var none var white var black\n"
                      << "uciok" << std::endl;
        } else if (token == "isready") {
            std::cout << "readyok" << std::endl;
        } else if (token == "ucinewgame") {
            join();
            Search::clear();
            NNUE::new_game();
        } else if (token == "setoption") {
            join();
            std::string name, value, t;
            is >> t;  // "name"
            while (is >> t && t != "value") name += (name.empty() ? "" : " ") + t;
            is >> value;
            if (name == "Hash") TT.resize(std::stoul(value));
            else if (name == "MultiPV") multiPV = std::clamp(std::stoi(value), 1, 8);
            else if (name == "MandatoryContinuation") Rules::mandatoryContinuation = value == "true";
            else if (name == "NoCaptureLimit") Rules::noCaptureLimit = std::stoi(value);
            else if (name == "UseNNUE") NNUE::set_enabled(value == "true");
            else if (name == "Quantized") NNUE::set_quantized(value == "true");
            else if (name == "EvalFile") {
                if (value != "<empty>") NNUE::load(value);
            } else if (name == "Variant") {
                // Nouvelle variante : nouvelle géométrie, on repart de la position initiale.
                set_variant(value == "dimy" ? Variant::Dimy : value == "telo" ? Variant::Telo : Variant::Tsivy);
                if (is_telo()) Telo::init();
                Search::clear();
                Position p;
                p.set(start_fen());
                game.reset(p);
                game.telo = Telo::State{};
                if (is_telo()) game.history.assign(1, game.telo.key());
            } else if (name == "Vela") {
                if (Rules::variant != Variant::Tsivy && value != "none")
                    std::cout << "info string la vela n'existe qu'en Fanoron-Tsivy" << std::endl;
                else {
                    Rules::vela = value == "white" ? WHITE : value == "black" ? BLACK : COLOR_NB;
                    Search::clear();
                    Position p;
                    p.set(start_fen());
                    game.reset(p);
                }
            } else std::cout << "info string unknown option " << name << std::endl;
        } else if (token == "position") {
            join();
            cmd_position(game, is);
        } else if (token == "go" && is_telo()) {
            SearchLimits limits = parse_limits(is);
            Telo::go(game.telo, limits.depth);
        } else if (token == "go") {
            join();
            SearchLimits limits = parse_limits(is);
            limits.multiPV = multiPV;
            Search::stopSignal = false;
            Game snapshot = game;
            searchThread = std::thread([snapshot, limits]() {
                auto r = Search::think(snapshot.pos, snapshot.history, limits, true);
                std::cout << "bestmove " << move_to_string(snapshot.pos, r.bestMove) << std::endl;
            });
            if (argc > 1) join();
        } else if (token == "d") {
            std::cout << (is_telo() ? Telo::pretty(game.telo) : game.pos.pretty()) << std::endl;
        } else if (token == "moves" && is_telo()) {
            for (const auto& m : Telo::moves(game.telo)) std::cout << m.notation << ' ';
            std::cout << std::endl;
        } else if (token == "moves") {
            for (const auto& dm : generate_detailed(game.pos)) std::cout << dm.notation << ' ';
            std::cout << std::endl;
        } else if ((token == "play" || token == "gensfen" || token == "bench" || token == "eval") && is_telo()) {
            std::cout << "info string commande indisponible en Fanoron-Telo" << std::endl;
        } else if (token == "eval") {
            std::cout << "Evaluation (camp au trait) : " << evaluate(game.pos) << std::endl;
        } else if (token == "status") {
            std::cout << "status " << game_status(game) << std::endl;
        } else if (token == "perft") {
            int d = 1;
            is >> d;
            if (is_telo()) std::cout << "Nodes searched: " << Telo::perft(game.telo, d) << std::endl;
            else cmd_perft(game.pos, d);
        } else if (token == "bench") {
            int d = 8;
            is >> d;
            cmd_bench(d);
        } else if (token == "play") {
            join();
            cmd_play(is);
        } else if (token == "gensfen") {
            join();
            cmd_gensfen(is);
        } else if (token == "help") {
            print_help();
        } else if (!token.empty()) {
            std::cout << "Unknown command: '" << cmd << "'. Type help for more information." << std::endl;
        }
    } while (token != "quit" && argc == 1);

    Search::stopSignal = true;
    join();
}

}  // namespace fanorona::UCI
