#include "nnue.h"

#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>

#include "evaluate.h"

namespace fanorona::NNUE {

namespace {

constexpr int INPUT_SIZE = 2 * SQUARE_NB;  // 90 : cases du camp au trait (0..44) + adverses (45..89)
// Doit correspondre EXACTEMENT à SCORE_SCALE dans tools/nnue/train.py : la sortie du réseau
// est un logit en unités score/SCORE_SCALE, reconverti ici en centipions.
constexpr double SCORE_SCALE = 400.0;

bool g_loaded = false;
bool g_wantEnabled = false;
int g_hidden = 0;
std::vector<float> g_w1;  // [hidden][INPUT_SIZE], row-major (= nn.Linear(90, hidden).weight)
std::vector<float> g_b1;  // [hidden]
std::vector<float> g_w2;  // [hidden]        (= nn.Linear(hidden, 1).weight, une seule ligne)
float g_b2 = 0.0f;

bool read_exact(std::ifstream& f, void* dst, size_t bytes) {
    f.read(reinterpret_cast<char*>(dst), std::streamsize(bytes));
    return bool(f) && size_t(f.gcount()) == bytes;
}

}  // namespace

bool load(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        std::cout << "info string nnue: impossible d'ouvrir " << path << std::endl;
        return false;
    }

    char magic[4];
    if (!read_exact(f, magic, 4) || std::string(magic, 4) != "FNUE") {
        std::cout << "info string nnue: fichier invalide (magic) " << path << std::endl;
        return false;
    }
    int32_t hidden = 0;
    if (!read_exact(f, &hidden, sizeof(hidden)) || hidden <= 0 || hidden > 65536) {
        std::cout << "info string nnue: taille de couche cachée invalide dans " << path << std::endl;
        return false;
    }

    size_t h = size_t(hidden);  // évite le "most vexing parse" de vector<float> w2(size_t(hidden))
    std::vector<float> w1(h * INPUT_SIZE), b1(h), w2(h);
    float b2 = 0.0f;
    bool ok = read_exact(f, w1.data(), w1.size() * sizeof(float)) &&
              read_exact(f, b1.data(), b1.size() * sizeof(float)) &&
              read_exact(f, w2.data(), w2.size() * sizeof(float)) && read_exact(f, &b2, sizeof(b2));
    if (!ok) {
        std::cout << "info string nnue: fichier tronqué " << path << std::endl;
        return false;
    }

    g_hidden = hidden;
    g_w1 = std::move(w1);
    g_b1 = std::move(b1);
    g_w2 = std::move(w2);
    g_b2 = b2;
    g_loaded = true;
    std::cout << "info string nnue: " << path << " chargé (hidden=" << hidden << ")" << std::endl;
    return true;
}

bool enabled() { return g_loaded && g_wantEnabled; }
void set_enabled(bool on) { g_wantEnabled = on; }

Value evaluate(const Position& pos) {
    Color us = pos.sideToMove;
    Bitboard own = pos.pieces(us), opp = pos.pieces(~us);

    std::vector<float> h(g_b1);  // copie : h[i] = biais initial

    for (Bitboard b = own; b;) {
        int sq = pop_lsb(b);
        for (int i = 0; i < g_hidden; ++i) h[size_t(i)] += g_w1[size_t(i) * INPUT_SIZE + sq];
    }
    for (Bitboard b = opp; b;) {
        int sq = pop_lsb(b);
        for (int i = 0; i < g_hidden; ++i) h[size_t(i)] += g_w1[size_t(i) * INPUT_SIZE + SQUARE_NB + sq];
    }

    double out = double(g_b2);
    for (int i = 0; i < g_hidden; ++i) {
        float a = h[size_t(i)] < 0.0f ? 0.0f : (h[size_t(i)] > 1.0f ? 1.0f : h[size_t(i)]);  // ClippedReLU [0,1]
        out += double(g_w2[size_t(i)]) * double(a);
    }

    return clamp_eval(int(std::lround(out * SCORE_SCALE)));
}

}  // namespace fanorona::NNUE
