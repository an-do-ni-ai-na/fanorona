"""Faisabilité des tables de finales, étape 3 : verdict de la recherche longue (eg_deep.py) contre le résultat réel.

    python3 eg_report.py endgames_deep.jsonl
"""
import collections
import json
import sys

rows = [json.loads(l) for l in open(sys.argv[1] if len(sys.argv) > 1 else "endgames_deep.jsonl")]
def actual(r):
    if r["result"].startswith("draw"): return "nulle"
    w = r["result"] == "white wins"
    return "gain" if (w == (r["stm"] == "w")) else "perte"
def verdict(r):
    if r.get("deep_kind") == "mate": return "gain forcé" if r["deep_val"] > 0 else "perte forcée"
    v = r.get("deep_val", 0)
    return "avantage" if v > 150 else "désavantage" if v < -150 else "équilibre"
m = collections.Counter((verdict(r), actual(r)) for r in rows)
print("verdict de la recherche longue (3 s, camp au trait) x résultat réel de la partie (100 ms) :")
for v in ("gain forcé", "avantage", "équilibre", "désavantage", "perte forcée"):
    n = sum(c for (a, b), c in m.items() if a == v)
    if n: print(f"  {v:13s} n={n:4d} : " + ", ".join(f"{b} {m[(v, b)]}" for b in ("gain", "nulle", "perte")))
forced = [r for r in rows if r.get("deep_kind") == "mate"]
wins = [r for r in forced if r["deep_val"] > 0]
miss = [r for r in wins if actual(r) != "gain"]
loss = [r for r in forced if r["deep_val"] < 0]
saved = [r for r in loss if actual(r) != "perte"]
print(f"\ngain forcé détecté : {len(wins)} ; non converti à 100 ms : {len(miss)} ({100*len(miss)/max(1,len(wins)):.0f} %)")
print(f"perte forcée détectée : {len(loss)} ; non exploitée par l'autre camp : {len(saved)} ({100*len(saved)/max(1,len(loss)):.0f} %)")
seen = sum(1 for r in wins if r.get("mate") is not None or (r.get("score") or 0) > 9000)
print(f"gains forcés que le moteur à 100 ms voyait déjà comme mat : {seen}/{len(wins)}")
print("longueur des mats (coups, plafonné à 30) :", sorted(collections.Counter(min(abs(r["deep_val"]), 30) for r in forced).items()))
print("profondeur atteinte en 3 s :", sorted(collections.Counter(r.get("deep_depth") for r in rows).items()))
print("\npar seuil d'entrée en finale :")
for k in (4, 5, 6):
    rk = [r for r in rows if r.get("k") == k]
    ws = [r for r in rk if r.get("deep_kind") == "mate" and r["deep_val"] > 0]; ms = [r for r in ws if actual(r) != "gain"]
    ls = [r for r in rk if r.get("deep_kind") == "mate" and r["deep_val"] < 0]; sv = [r for r in ls if actual(r) != "perte"]
    eq = [r for r in rk if verdict(r) == "équilibre"]; eqd = [r for r in eq if actual(r) != "nulle"]
    av = [r for r in rk if verdict(r) == "avantage"]; avm = [r for r in av if actual(r) != "gain"]
    print(f"  ≤ {k} pièces ({len(rk)} pos.) : mats {len(ws)}+{len(ls)} ; gains forcés non convertis {len(ms)}/{len(ws)}, "
          f"pertes forcées sauvées {len(sv)}/{len(ls)} ; avantage non converti {len(avm)}/{len(av)} ; équilibre perdu {len(eqd)}/{len(eq)}")
print("\nexemples de gains manqués :")
for r in miss[:6]:
    print(f"  {r['fen']}  mat en {r['deep_val']}, coup profond {r['deep_best']}, joué {r['move']} (score à 100 ms {r['score']}), résultat {r['result']}")
