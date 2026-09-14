#!/usr/bin/env python3
"""
PokeIsland Updater — Linux
============================

Outil de synchronisation automatique du modpack PokeIsland pour Prism Launcher
(Flatpak ou natif). Le script :

  * crée l'instance Prism Launcher (Minecraft + Fabric) si elle n'existe pas ;
  * interroge l'API PokeIsland pour obtenir la liste des fichiers à jour ;
  * télécharge en parallèle les fichiers manquants ou modifiés ;
  * peut nettoyer les fichiers obsolètes (mode automatique ou interactif).

Aucune dépendance tierce n'est requise (bibliothèque standard uniquement).
Compatible Python 3.8+.

Usage :
    python3 pokeisland_linux.py [--name NOM] [--clean | --interactive]
                                 [--dry-run] [--threads N]

Licence : MIT
"""

from __future__ import annotations

import argparse
import configparser
import fnmatch
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

VERSION = "1.5.0"
API_URL = "https://launcher-api.pokeisland.fr/api/v1/service/update/files"

# Logo officiel du serveur, présent dans les fichiers synchronisés (dossier "config/",
# donc déjà téléchargé par le sync normal). Utilisé comme icône de l'instance Prism Launcher.
SERVER_LOGO_RELATIVE_PATH = "config/fancymenu/assets/pokeisland_logo.png"
PRISM_ICON_KEY = "pokeisland_logo"

IGNORED_DIRS = ("assets/", "libraries/", "versions/", "java/", "logs/")
IGNORE_FILE_NAME = ".pokeignore"
STATE_FILE_NAME = "poke_state.json"
TRACKED_DIRS = ("mods", "config", "defaultconfigs", "fancymenu_data", "shaderpacks", "showdown", "xaero")

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5
REQUEST_TIMEOUT = 30  # secondes

MINECRAFT_VERSION = "1.21.1"
FABRIC_INTERMEDIARY_VERSION = "1.21.1"
FABRIC_LOADER_VERSION = "0.19.5"

# Les couleurs ANSI sont désactivées automatiquement si la sortie est redirigée
# (fichier de log, pipe), afin de ne pas polluer les fichiers de sortie.
_USE_COLOR = sys.stdout.isatty()


def _c(code: str) -> str:
    return code if _USE_COLOR else ""


GREEN = _c("\033[92m")
YELLOW = _c("\033[93m")
RED = _c("\033[91m")
CYAN = _c("\033[96m")
RESET = _c("\033[0m")
BOLD = _c("\033[1m")

print_lock = threading.Lock()


def safe_print(msg: str) -> None:
    """Affiche un message de manière sécurisée en multithreading."""
    with print_lock:
        print(msg)


def get_prism_instances_dir() -> str:
    """Détecte automatiquement si Prism Launcher est en version Flatpak ou Native."""
    flatpak_path = os.path.expanduser(
        "~/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/instances"
    )
    native_path = os.path.expanduser("~/.local/share/PrismLauncher/instances")

    if os.path.exists(flatpak_path):
        safe_print(f"{CYAN}🔍 Prism Launcher détecté : Format Flatpak{RESET}")
        return flatpak_path

    safe_print(f"{CYAN}🔍 Prism Launcher détecté : Format Natif{RESET}")
    return native_path


def ensure_pokeignore(mc_dir: str, dry_run: bool = False) -> None:
    """Crée .pokeignore s'il n'existe pas, que l'instance soit neuve ou déjà présente."""
    ignore_path = os.path.join(mc_dir, IGNORE_FILE_NAME)
    if os.path.exists(ignore_path):
        return

    if dry_run:
        safe_print(f"{YELLOW}[DRY-RUN] 🧪 Création virtuelle de {IGNORE_FILE_NAME}{RESET}")
        return

    os.makedirs(mc_dir, exist_ok=True)
    with open(ignore_path, "w", encoding="utf-8") as f:
        f.write("# Liste des fichiers ou dossiers personnels a proteger lors du nettoyage (--clean)\n")
        f.write("# Motifs acceptes : chemin exact, prefixe de dossier, ou glob (*, ?, [...])\n")
        f.write("# Exemple :\n")
        f.write("# mods/mon_mod_perso.jar\n")
        f.write("# config/mon_custom_config.json\n")


def setup_prism_instance(instance_name: str, mc_dir: str, instance_dir: str, dry_run: bool = False) -> None:
    """Crée l'instance Prism Launcher avec Fabric si elle n'existe pas déjà."""
    if os.path.exists(instance_dir):
        ensure_pokeignore(mc_dir, dry_run=dry_run)
        return

    if dry_run:
        safe_print(f"{YELLOW}[DRY-RUN] 🧪 Création virtuelle de l'instance Prism Launcher '{instance_name}'{RESET}")
        return

    safe_print(f"{GREEN}✨ Création automatique de l'instance Prism Launcher '{instance_name}'...{RESET}")
    os.makedirs(mc_dir, exist_ok=True)

    cfg_content = f"""[General]
ConfigVersion=1.2
iconKey=default
name={instance_name}
"""
    with open(os.path.join(instance_dir, "instance.cfg"), "w", encoding="utf-8") as f:
        f.write(cfg_content)

    mmc_pack: dict[str, Any] = {
        "components": [
            {"important": True, "uid": "net.minecraft", "version": MINECRAFT_VERSION},
            {"uid": "net.fabricmc.intermediary", "version": FABRIC_INTERMEDIARY_VERSION},
            {"uid": "net.fabricmc.fabric-loader", "version": FABRIC_LOADER_VERSION},
        ],
        "formatVersion": 1,
    }
    with open(os.path.join(instance_dir, "mmc-pack.json"), "w", encoding="utf-8") as f:
        json.dump(mmc_pack, f, indent=4)

    ensure_pokeignore(mc_dir, dry_run=dry_run)

    safe_print(
        f"{GREEN}✅ Instance créée avec succès ! Minecraft {MINECRAFT_VERSION}, "
        f"Fabric et {IGNORE_FILE_NAME} configurés.{RESET}"
    )


def update_instance_icon(instance_dir: str, mc_dir: str, instances_dir: str, dry_run: bool = False) -> bool:
    """Copie le logo du serveur dans le dossier d'icônes de Prism Launcher et met à jour
    l'iconKey de l'instance. Ne fait rien si le logo n'a pas encore été synchronisé
    (ex: tout premier lancement, avant la phase de téléchargement)."""
    logo_src = os.path.join(mc_dir, SERVER_LOGO_RELATIVE_PATH)
    if not os.path.exists(logo_src):
        return False

    if dry_run:
        safe_print(f"{YELLOW}[DRY-RUN] 🎨 [Simulation] Mise à jour de l'icône de l'instance avec le logo du serveur{RESET}")
        return True

    # Le dossier "icons/" est un voisin de "instances/" dans les données de Prism Launcher.
    icons_dir = os.path.join(os.path.dirname(os.path.normpath(instances_dir)), "icons")
    os.makedirs(icons_dir, exist_ok=True)
    icon_dest = os.path.join(icons_dir, f"{PRISM_ICON_KEY}.png")

    try:
        shutil.copyfile(logo_src, icon_dest)
    except OSError as e:
        safe_print(f"{RED}⚠️ Impossible de copier le logo vers {icon_dest} : {e}{RESET}")
        return False

    cfg_path = os.path.join(instance_dir, "instance.cfg")
    config = configparser.RawConfigParser()
    config.optionxform = str  # préserve la casse des clés (iconKey, ConfigVersion, ...)
    if os.path.exists(cfg_path):
        try:
            config.read(cfg_path, encoding="utf-8")
        except configparser.Error as e:
            safe_print(f"{RED}⚠️ Impossible de lire instance.cfg : {e}{RESET}")
            return False

    if "General" not in config:
        config["General"] = {}

    previous_icon = config["General"].get("iconKey")
    if previous_icon == PRISM_ICON_KEY:
        return True  # déjà à jour

    config["General"]["iconKey"] = PRISM_ICON_KEY
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            config.write(f, space_around_delimiters=False)
    except OSError as e:
        safe_print(f"{RED}⚠️ Impossible d'écrire instance.cfg : {e}{RESET}")
        return False

    safe_print(f"{GREEN}🎨 Icône de l'instance mise à jour avec le logo du serveur.{RESET}")
    return True


def load_ignore_patterns(mc_dir: str) -> set[str]:
    """Charge les motifs d'exclusion depuis .pokeignore."""
    ignore_path = os.path.join(mc_dir, IGNORE_FILE_NAME)
    patterns: set[str] = set()
    if os.path.exists(ignore_path):
        with open(ignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line)
    return patterns


def is_ignored(rel_path: str, ignore_patterns: set[str]) -> bool:
    """Vérifie si un fichier correspond à une règle d'exclusion (chemin exact, dossier ou glob)."""
    rel_path_posix = rel_path.replace(os.sep, "/")
    for pattern in ignore_patterns:
        pattern_posix = pattern.replace(os.sep, "/")
        if rel_path_posix == pattern_posix:
            return True
        if rel_path_posix.startswith(pattern_posix.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(rel_path_posix, pattern_posix):
            return True
    return False


def load_state(mc_dir: str) -> dict:
    """Charge le cache d'état local."""
    state_path = os.path.join(mc_dir, STATE_FILE_NAME)
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(mc_dir: str, state: dict) -> None:
    """Sauvegarde le cache d'état local."""
    state_path = os.path.join(mc_dir, STATE_FILE_NAME)
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
    except OSError as e:
        safe_print(f"{RED}⚠️ Impossible d'enregistrer le cache d'état : {e}{RESET}")


def download_file(file_info: dict, mc_dir: str, dry_run: bool = False) -> bool:
    """Télécharge un fichier avec tentatives, timeout et vérification de taille."""
    relative_path = file_info["fileRelativePath"]
    download_url = file_info["tmpSignedUrl"]
    expected_size = file_info.get("size")
    local_path = os.path.join(mc_dir, relative_path)

    if dry_run:
        safe_print(f"{YELLOW}[DRY-RUN] ⬇️ [Simulation] Téléchargement : {relative_path}{RESET}")
        return True

    for attempt in range(1, MAX_RETRIES + 1):
        tmp_path = local_path + ".part"
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
            directory = os.path.dirname(local_path)
            if directory:
                os.makedirs(directory, exist_ok=True)

            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response, open(tmp_path, "wb") as out_file:
                out_file.write(response.read())

            actual_size = os.path.getsize(tmp_path)
            if expected_size is not None and actual_size != expected_size:
                raise IOError(f"taille inattendue ({actual_size} reçus, {expected_size} attendus)")

            os.replace(tmp_path, local_path)
            safe_print(f"{GREEN}⬇️ Mis à jour :{RESET} {relative_path}")
            return True
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if attempt == MAX_RETRIES:
                safe_print(f"{RED}❌ Échec définitif pour {relative_path}: {e}{RESET}")
                return False
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    return False


def fetch_manifest() -> dict:
    """Récupère la liste des fichiers depuis l'API PokeIsland."""
    safe_print(f"{CYAN}🔍 Connexion à l'API PokeIsland...{RESET}")
    req = urllib.request.Request(API_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        safe_print(f"{RED}❌ Erreur lors de la connexion à l'API : {e}{RESET}")
        sys.exit(1)


def plan_sync(files_to_check: list, mc_dir: str) -> tuple[set, list, dict]:
    """Détermine quels fichiers doivent être (re)téléchargés."""
    server_files: set[str] = set()
    files_to_download: list[dict] = []
    local_state = load_state(mc_dir)
    new_state: dict[str, int] = {}

    for file_info in files_to_check:
        relative_path = file_info["fileRelativePath"]
        remote_size = file_info.get("size", 0)

        if relative_path.startswith(IGNORED_DIRS) or ".fabric/" in relative_path:
            continue

        server_files.add(relative_path)
        new_state[relative_path] = remote_size

        local_path = os.path.join(mc_dir, relative_path)
        needs_download = True
        if os.path.exists(local_path):
            local_size = os.path.getsize(local_path)
            if local_size == remote_size and local_state.get(relative_path) == remote_size:
                needs_download = False

        if needs_download:
            files_to_download.append(file_info)

    return server_files, files_to_download, new_state


def run_downloads(files_to_download: list, mc_dir: str, threads: int, dry_run: bool) -> int:
    """Télécharge les fichiers en parallèle et renvoie le nombre de succès."""
    if not files_to_download:
        safe_print(f"{GREEN}✨ Tout est déjà à jour ! Aucun téléchargement nécessaire.{RESET}")
        return 0

    safe_print(
        f"{CYAN}🚀 Téléchargement de {len(files_to_download)} fichier(s) mis à jour "
        f"({threads} thread(s))...{RESET}"
    )
    download_count = 0
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {executor.submit(download_file, f_info, mc_dir, dry_run): f_info for f_info in files_to_download}
        for future in as_completed(futures):
            if future.result():
                download_count += 1
    return download_count


def _ask_deletion_choice(rel_path: str) -> str:
    """Pose la question de suppression et normalise la réponse en 'yes'/'no'/'all_yes'/'all_no'."""
    raw = input(
        f"{YELLOW}🗑️ Fichier obsolète détecté : {rel_path}. "
        f"Le supprimer ? [o]ui / [N]on / [t]out oui / [a]tout non : {RESET}"
    ).strip().lower()

    if raw in ("t", "tout"):
        return "all_yes"
    if raw == "a":
        return "all_no"
    if raw in ("o", "oui", "y", "yes"):
        return "yes"
    return "no"  # couvre 'n', 'non', '' et toute entrée non reconnue (choix sûr par défaut)


def cleanup_obsolete_files(mc_dir: str, server_files: set, mode: str, dry_run: bool) -> int:
    """Supprime (ou simule) les fichiers absents du serveur. mode : 'clean' ou 'interactive'."""
    safe_print(f"{CYAN}🧹 Analyse des fichiers obsolètes...{RESET}")
    ignore_patterns = load_ignore_patterns(mc_dir)

    deleted_count = 0
    interactive_all_yes = False
    interactive_all_no = False

    for d in TRACKED_DIRS:
        full_dir_path = os.path.join(mc_dir, d)
        if not os.path.exists(full_dir_path):
            continue

        for root, _, files in os.walk(full_dir_path):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, mc_dir)

                if is_ignored(rel_path, ignore_patterns) or rel_path in server_files:
                    continue

                should_delete = False
                if dry_run:
                    safe_print(f"{YELLOW}[DRY-RUN] 🗑️ [Simulation] Suppression : {rel_path}{RESET}")
                    should_delete = True
                elif mode == "clean":
                    safe_print(f"{RED}🗑️ Suppression obsolète :{RESET} {rel_path}")
                    should_delete = True
                elif mode == "interactive":
                    if interactive_all_yes:
                        should_delete = True
                    elif interactive_all_no:
                        should_delete = False
                    else:
                        choice = _ask_deletion_choice(rel_path)
                        if choice == "all_yes":
                            interactive_all_yes = True
                            should_delete = True
                        elif choice == "all_no":
                            interactive_all_no = True
                            should_delete = False
                        else:
                            should_delete = choice == "yes"

                if should_delete:
                    if not dry_run:
                        os.remove(abs_path)
                    deleted_count += 1

    return deleted_count


def print_summary(download_count: int, deleted_count: int | None, dry_run: bool) -> None:
    safe_print(f"\n{GREEN}{BOLD}✅ Synchronisation terminée avec succès !{RESET}")
    if dry_run:
        safe_print(f"{YELLOW}   (Mode Dry-Run actif : aucune modification réelle n'a été effectuée){RESET}")
    safe_print(f"{CYAN}   - Fichiers téléchargés : {download_count}{RESET}")
    if deleted_count is not None:
        safe_print(f"{CYAN}   - Fichiers obsolètes nettoyés : {deleted_count}{RESET}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mise à jour automatique du serveur PokeIsland pour Prism Launcher.")
    parser.add_argument("--name", type=str, default="PokeIsland", help="Nom de l'instance dans Prism Launcher")

    cleanup_group = parser.add_mutually_exclusive_group()
    cleanup_group.add_argument(
        "--clean", action="store_true",
        help="Supprime automatiquement les fichiers obsolètes absents du serveur",
    )
    cleanup_group.add_argument(
        "--interactive", "-i", action="store_true",
        help="Demande confirmation avant de supprimer chaque fichier obsolète",
    )

    parser.add_argument("--dry-run", action="store_true", help="Simule la mise à jour sans modifier le disque")
    parser.add_argument("--threads", type=int, default=6, help="Nombre de téléchargements simultanés (défaut: 6)")
    parser.add_argument(
        "--no-icon-update", action="store_true",
        help="Ne pas mettre à jour l'icône de l'instance avec le logo du serveur",
    )
    parser.add_argument("--version", "-v", action="version", version=f"PokeIsland Updater v{VERSION}")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.threads < 1:
        parser.error("--threads doit être supérieur ou égal à 1.")
    if os.sep in args.name or (os.altsep and os.altsep in args.name):
        parser.error("--name ne doit pas contenir de séparateurs de chemin.")

    instances_dir = get_prism_instances_dir()
    instance_dir = os.path.join(instances_dir, args.name)
    mc_dir = os.path.join(instance_dir, ".minecraft")

    setup_prism_instance(args.name, mc_dir, instance_dir, dry_run=args.dry_run)

    manifest = fetch_manifest()
    files_to_check = manifest.get("filesInfoList", [])
    safe_print(f"{CYAN}📦 {len(files_to_check)} fichiers référencés sur le serveur.{RESET}")

    server_files, files_to_download, new_state = plan_sync(files_to_check, mc_dir)
    download_count = run_downloads(files_to_download, mc_dir, args.threads, args.dry_run)

    if not args.dry_run:
        save_state(mc_dir, new_state)

    if not args.no_icon_update:
        update_instance_icon(instance_dir, mc_dir, instances_dir, dry_run=args.dry_run)

    deleted_count = None
    if args.clean or args.interactive:
        mode = "clean" if args.clean else "interactive"
        deleted_count = cleanup_obsolete_files(mc_dir, server_files, mode, args.dry_run)

    print_summary(download_count, deleted_count, args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        safe_print(f"\n{RED}⛔ Interrompu par l'utilisateur.{RESET}")
        sys.exit(130)
