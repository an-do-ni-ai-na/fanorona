// Fanorona engine - protocole texte inspiré de l'UCI
#pragma once

#include <string>

namespace fanorona::UCI {
void loop(int argc, char* argv[]);
// Session persistante (WebAssembly) : exécute une commande de façon synchrone, renvoie sa sortie.
std::string execute(const std::string& cmd);
}
