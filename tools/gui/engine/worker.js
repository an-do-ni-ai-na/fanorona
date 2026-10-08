// Moteur Fanorona en WebAssembly (jeu hors ligne de l'interface) : fanorona.js + .wasm (version SIMD si le
// navigateur la gère), réseau NNUE par défaut du serveur (/engine/net.nnue). Une session persistante : chaque
// message {id, cmds: [...]} exécute les commandes dans l'ordre (même protocole que la ligne de commande,
// recherche synchrone) et renvoie {id, out} (sortie concaténée) ou {id, error}.
// Détection de WebAssembly SIMD (module minimal utilisant un v128) :
const SIMD = WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]));
importScripts("/engine/fanorona.js");
const ready = (async () => {
  const M = await FanoronaEngine({ locateFile: f => "/engine/" + (f.endsWith(".wasm") && SIMD ? "fanorona-simd.wasm" : f) });
  M._fanorona_init(32);
  const cmd = M.cwrap("fanorona_cmd", "string", ["string"]);
  let net = false;
  try {
    const r = await fetch("/engine/net.nnue");
    if (r.ok) {
      M.FS.writeFile("/net.nnue", new Uint8Array(await r.arrayBuffer()));
      net = /chargé/.test(cmd("setoption name EvalFile value /net.nnue"));
    }
  } catch (e) { /* pas de réseau : évaluation classique */ }
  return { cmd, net };
})();
onmessage = async e => {
  const { id, cmds } = e.data;
  try {
    const { cmd, net } = await ready;
    if (cmds === "info") return postMessage({ id, out: JSON.stringify({ simd: SIMD, net }) });
    postMessage({ id, out: cmds.map(c => cmd(c)).join("") });
  } catch (err) { postMessage({ id, error: String(err && err.message || err) }); }
};
