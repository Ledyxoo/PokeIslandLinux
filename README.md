# PokeIsland Updater (Linux)

Outil de synchronisation automatique du modpack **PokeIsland** pour [Prism Launcher](https://prismlauncher.org/) sous Linux (Flatpak ou natif).

🔗 Site officiel du serveur : [pokeisland.fr](https://pokeisland.fr/)

> **PokeIsland ne propose officiellement pas de launcher pour Linux** (uniquement Windows/Mac). Ce script comble ce manque : il permet aux joueurs Linux d'installer, mettre à jour et jouer sur le serveur PokeIsland via Prism Launcher, sans dépendre d'un launcher officiel non disponible sur cette plateforme.

Le script :

- crée automatiquement l'instance Prism Launcher (Minecraft + Fabric) si elle n'existe pas ;
- interroge l'API PokeIsland pour connaître la liste des fichiers à jour ;
- télécharge en parallèle les fichiers manquants ou modifiés ;
- peut nettoyer les fichiers obsolètes, en mode automatique ou interactif.

Aucune dépendance tierce n'est nécessaire : seule la bibliothèque standard Python est utilisée.

## Prérequis

- Linux
- Python 3.8 ou supérieur
- [Prism Launcher](https://prismlauncher.org/download/) installé (version Flatpak ou native — détectée automatiquement)

Vérifier la version de Python installée :

```bash
python3 --version
```

## Installation

```bash
# Télécharger le script
curl -O https://raw.githubusercontent.com/Ledyxoo/PokeIslandLinux/main/PokeIslandLinux.py
```

## Utilisation

Mise à jour simple (télécharge les fichiers manquants ou modifiés) :

```bash
python3 PokeIslandLinux.py
```

Premier lancement : le script crée automatiquement l'instance `PokeIsland` dans Prism Launcher, avec Minecraft et Fabric déjà configurés. Il suffit ensuite de lancer l'instance depuis Prism Launcher.

### Options disponibles

| Option | Description |
|---|---|
| `--name NOM` | Nom de l'instance Prism Launcher à utiliser (défaut : `PokeIsland`) |
| `--clean` | Supprime automatiquement les fichiers obsolètes absents du serveur |
| `--interactive`, `-i` | Demande confirmation avant de supprimer chaque fichier obsolète |
| `--dry-run` | Simule la mise à jour sans modifier le disque (aucun téléchargement ni suppression réel) |
| `--threads N` | Nombre de téléchargements simultanés (défaut : 6) |
| `--no-icon-update` | Ne pas mettre à jour l'icône de l'instance avec le logo du serveur |
| `--version`, `-v` | Affiche la version du script |
| `--help`, `-h` | Affiche l'aide |

> `--clean` et `--interactive` sont mutuellement exclusifs.

### Exemples

Simuler une mise à jour complète avant de l'appliquer pour de vrai :

```bash
python3 PokeIslandLinux.py --dry-run --clean
```

Mettre à jour une instance nommée différemment, avec nettoyage interactif :

```bash
python3 PokeIslandLinux.py --name "PokeIsland-Test" --interactive
```

Accélérer les téléchargements sur une bonne connexion :

```bash
python3 PokeIslandLinux.py --threads 12
```

## Protéger des fichiers personnels avec `.pokeignore`

À la création de l'instance, un fichier `.pokeignore` est généré dans le dossier `.minecraft/` de l'instance. Il permet d'exclure certains fichiers ou dossiers du nettoyage effectué par `--clean` / `--interactive` (par exemple vos propres mods ou configurations personnalisées).

Chaque ligne peut contenir :

- un chemin exact : `mods/mon_mod_perso.jar`
- un dossier entier (tout son contenu est protégé) : `config/mon_dossier/`
- un motif glob : `mods/*.jar`, `shaderpacks/perso-*.zip`

Les lignes commençant par `#` sont des commentaires et sont ignorées.

Exemple de fichier `.pokeignore` :

```
# Mods et configs personnels à ne jamais supprimer
mods/mon_mod_perso.jar
config/mon_dossier_perso/
shaderpacks/perso-*.zip
```

## Icône de l'instance

Après chaque synchronisation, le script copie automatiquement le logo officiel du serveur (`config/fancymenu/assets/pokeisland_logo.png`, déjà présent parmi les fichiers synchronisés) dans le dossier d'icônes de Prism Launcher, et configure l'instance pour l'utiliser. Aucune action manuelle n'est nécessaire.

Pour désactiver ce comportement (par exemple si vous avez déjà personnalisé l'icône) :

```bash
python3 PokeIslandLinux.py --no-icon-update
```

## Où sont installés les fichiers ?

Le script détecte automatiquement l'emplacement des instances Prism Launcher :

- **Flatpak** : `~/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/instances`
- **Native** : `~/.local/share/PrismLauncher/instances`

Les fichiers du modpack sont ensuite synchronisés dans `<instance>/.minecraft/`.

## Fonctionnement du nettoyage (`--clean` / `--interactive`)

Le nettoyage ne s'applique qu'aux dossiers suivis : `mods`, `config`, `defaultconfigs`, `fancymenu_data`, `shaderpacks`, `showdown`, `xaero`. Un fichier y est considéré comme obsolète s'il n'est plus référencé par le serveur et n'est pas protégé par `.pokeignore`.

- `--clean` : supprime automatiquement tous les fichiers obsolètes, sans confirmation.
- `--interactive` : demande confirmation pour chaque fichier, avec les choix suivants :
  - `o` (oui) : supprimer ce fichier uniquement
  - `N` (non, par défaut) : garder ce fichier uniquement
  - `t` (tout oui) : supprimer ce fichier et tous les suivants sans redemander
  - `a` (tout non) : garder ce fichier et tous les suivants sans redemander

Sans aucune de ces deux options, le script ne touche à aucun fichier existant (il télécharge uniquement les mises à jour).

Bon jeu !
