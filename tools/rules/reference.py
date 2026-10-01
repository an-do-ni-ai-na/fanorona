#!/usr/bin/env python3
"""Générateur de coups de référence, indépendant du moteur, pour valider ses règles.

Écrit directement d'après les règles (README, « Règles implémentées »), volontairement naïf et lisible : points en
coordonnées (x, y), ensembles Python, aucun bitboard ni aucune table du moteur. Sert à confronter le moteur :

  - perft (nombre de coups distincts — mêmes (départ, arrivée, pièces prises) fusionnés, comme le moteur) ;
  - liste complète des coups notés (chemin + A/W) sur des positions réelles (tools/rules/crosscheck.py).

Règles appliquées :
  - points (x, y), x = a.. , y = 1.. ; lignes orthogonales partout, diagonales seulement depuis/vers les points
    « forts » (x + y pair) ;
  - capture par approche : la pièce va de s à t (voisin vide, direction d) et le point t + d porte une pièce
    adverse -> cette pièce et toutes les pièces adverses contiguës dans la direction d sont prises ;
    capture par retrait : le point s - d porte une pièce adverse -> idem dans la direction -d ;
  - les deux à la fois possibles : ce sont deux coups distincts (choix du joueur) ;
  - captures obligatoires : s'il en existe une, seuls les coups capturants sont permis ;
  - après une capture, la même pièce peut continuer (pièces prises retirées aussitôt), sans revenir sur un point
    déjà visité pendant le tour (départ compris) ni repartir dans la même direction que l'étape précédente ;
    arrêt libre à tout moment (ou obligation de continuer tant que possible, variante « continuation ») ;
  - sans capture : déplacement simple (paika) vers tout voisin vide.
"""
import argparse

DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
FILES = "abcdefghi"


class Board:
    def __init__(self, files, ranks):
        self.files, self.ranks = files, ranks

    def inside(self, x, y):
        return 0 <= x < self.files and 0 <= y < self.ranks

    def neighbour(self, p, d):
        (x, y), (dx, dy) = p, d
        if dx and dy and (x + y) % 2:  # diagonale depuis un point faible : pas de ligne
            return None
        q = (x + dx, y + dy)
        return q if self.inside(*q) else None


def parse_fen(fen):
    rows, side = fen.split()[:2]
    rows = rows.split("/")
    ranks = len(rows)
    files = sum(int(c) if c.isdigit() else 1 for c in rows[0])
    pieces = {}
    for i, row in enumerate(rows):
        y, x = ranks - 1 - i, 0
        for c in row:
            if c.isdigit():
                x += int(c)
            else:
                pieces[(x, y)] = c
                x += 1
    return Board(files, ranks), pieces, "W" if side == "w" else "B"


def name(p):
    return FILES[p[0]] + str(p[1] + 1)


def line_of(board, pieces, start, d, them):
    """Pièces adverses contiguës à partir de `start` dans la direction d."""
    out, p = [], start
    while p is not None and pieces.get(p) == them:
        out.append(p)
        p = board.neighbour(p, d)
    return out


def captures_from(board, pieces, s, us, them, visited, last_dir):
    """Étapes de capture possibles depuis s : liste (t, direction, type 'A'/'W', pièces prises)."""
    steps = []
    for d in DIRS:
        if d == last_dir:
            continue
        t = board.neighbour(s, d)
        if t is None or t in pieces or t in visited:
            continue
        ahead = board.neighbour(t, d)
        if ahead is not None and pieces.get(ahead) == them:
            steps.append((t, d, "A", line_of(board, pieces, ahead, d, them)))
        back_d = (-d[0], -d[1])
        behind = board.neighbour(s, back_d)
        if behind is not None and pieces.get(behind) == them:
            steps.append((t, d, "W", line_of(board, pieces, behind, back_d, them)))
    return steps


def generate(board, pieces, us, mandatory=False):
    """Tous les coups : liste de (notation, départ, arrivée, frozenset des pièces prises)."""
    them = "B" if us == "W" else "W"
    moves = []

    def dfs(pieces, start, cur, visited, last_dir, path, taken):
        steps = captures_from(board, pieces, cur, us, them, visited, last_dir)
        for t, d, kind, caught in steps:
            nxt = dict(pieces)
            del nxt[cur]
            nxt[t] = us
            for c in caught:
                del nxt[c]
            p2 = path + name(t) + kind
            t2 = taken | frozenset(caught)
            if not mandatory:
                moves.append((p2, start, t, t2))
            dfs(nxt, start, t, visited | {t}, d, p2, t2)
        if mandatory and not steps and path != name(start):
            moves.append((path, start, cur, taken))

    for s, c in sorted(pieces.items()):
        if c == us:
            dfs(pieces, s, s, {s}, None, name(s), frozenset())
    if moves:
        return moves
    for s, c in sorted(pieces.items()):  # paika
        if c != us:
            continue
        for d in DIRS:
            t = board.neighbour(s, d)
            if t is not None and t not in pieces:
                moves.append((name(s) + name(t), s, t, frozenset()))
    return moves


def apply(pieces, us, mv):
    _, s, t, taken = mv
    nxt = {p: c for p, c in pieces.items() if p not in taken and p != s}
    nxt[t] = us
    return nxt


def perft(board, pieces, us, depth, mandatory=False):
    moves = generate(board, pieces, us, mandatory)
    distinct = {}
    for m in moves:  # même (départ, arrivée, prises) = même coup, comme dans le moteur
        distinct.setdefault((m[1], m[2], m[3]), m)
    if depth == 1:
        return len(distinct)
    them = "B" if us == "W" else "W"
    return sum(perft(board, apply(pieces, us, m), them, depth - 1, mandatory) for m in distinct.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fen", default="BBBBBBBBB/BBBBBBBBB/BWBW1BWBW/WWWWWWWWW/WWWWWWWWW w")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--mandatory", action="store_true")
    a = ap.parse_args()
    board, pieces, us = parse_fen(a.fen)
    for d in range(1, a.depth + 1):
        print(d, perft(board, pieces, us, d, a.mandatory), flush=True)


if __name__ == "__main__":
    main()
