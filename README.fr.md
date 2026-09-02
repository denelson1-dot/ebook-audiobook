*[English](README.md) · [Français](README.fr.md)*

<p align="center">
  <img src="ebook_audiobook/assets/icon-128.png" width="88" height="88" alt="">
</p>

<h1 align="center">ebook·audiobook</h1>

<p align="center">
  <strong>Transformez un livre numérique qui vous appartient en livre audio narré, entièrement sur votre propre machine.</strong><br>
  Un seul <code>.m4b</code> par livre, chapitré et étiqueté — rangé directement dans votre bibliothèque Plex.
</p>

<p align="center">
  <a href="https://github.com/denelson1-dot/ebook-audiobook/releases/latest"><img src="https://img.shields.io/github/v/release/denelson1-dot/ebook-audiobook?label=version&color=d98a5a" alt="Dernière version"></a>
  <a href="https://github.com/denelson1-dot/ebook-audiobook/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/denelson1-dot/ebook-audiobook/ci.yml?branch=main&label=CI" alt="État de la CI"></a>
  <img src="https://img.shields.io/badge/plateformes-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-555" alt="Windows, macOS, Linux">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab" alt="Python 3.11+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-MIT-2f7d4f" alt="Licence MIT"></a>
</p>

<p align="center">
  <a href="#installation">Installation</a> ·
  <a href="#utilisation">Utilisation</a> ·
  <a href="#langues">Langues</a> ·
  <a href="#écouter-le-résultat">Écouter le résultat</a> ·
  <a href="#combien-de-temps-ça-prend">Combien de temps ça prend</a> ·
  <a href="#comment-ça-marche">Comment ça marche</a> ·
  <a href="#usage-responsable-et-vie-privée">Vie privée</a>
</p>

<p align="center">
  <img src="docs/screenshots/library.png" alt="La bibliothèque : un livre en cours de narration, quatre terminés, un en attente" width="100%">
</p>

Pas de cloud, pas de compte, pas de télémétrie. Le modèle vocal tourne sur votre
GPU (NVIDIA, AMD ou Apple Silicon) ou, plus lentement, sur le processeur, et
rien de ce que vous convertissez ne quitte l'ordinateur.

## Pourquoi celui-ci

- **Un vrai livre audio, pas une synthèse vocale brute.** Un `.m4b` par livre,
  avec un marqueur par chapitre, la couverture intégrée et les étiquettes que
  Plex et Audiobookshelf attendent. Rangé sous la forme `Auteur / Titre (Année) / Titre.m4b`.
- **Un narrateur que vous choisiriez.** Sept voix du domaine public sont
  fournies, réglées à l'oreille — cinq anglaises, deux françaises. Ou clonez
  un extrait dont vous avez les droits, en une dizaine de secondes d'audio.
- **Honnête sur les heures.** Une narration prend du temps ; l'application
  mesure votre machine, vous dit combien, et garde la progression visible sur
  chaque écran. Fermez la fenêtre et elle continue dans la barre des tâches.
- **Rien n'est jamais perdu.** Chaque phrase est mise en cache par son contenu :
  une narration arrêtée reprend là où elle s'est arrêtée, et un réglage modifié
  ne renarre que ce qui a changé.
- **Elle fait le ménage derrière elle, quand vous le dites.** L'audio de travail
  derrière un livre pèse des gigaoctets ; l'application sait ce qui peut partir
  sans risque et ce qui permet une reprise, et ne supprime ni l'un ni l'autre
  sans qu'on le lui demande.
- **Une seule commande pour installer**, sur Windows, macOS et Linux. Elle
  installe son propre Python, choisit la bonne version de PyTorch pour votre
  matériel, et embarque ffmpeg.
- **En français.** L'interface suit la langue de votre navigateur (ou celle que
  vous choisissez dans les Réglages), et les livres en français sont narrés en
  français, par des voix françaises — voir [Langues](#langues).

## À quoi ça ressemble

<p align="center">
  <img src="docs/screenshots/book.png" alt="Un livre avant narration : quelles sections lire, quelle voix, et ce que ça coûtera en temps et en disque" width="100%">
  <br><sub>La page d'un livre. Ce qui sera narré à gauche, qui le narre à droite, et une barre en bas qui dit toujours à quoi vous vous engagez.</sub>
</p>

<p align="center">
  <img src="docs/screenshots/storage.png" alt="La page Stockage : quels fichiers de travail peuvent être libérés et lesquels permettent une reprise" width="100%">
  <br><sub>Stockage. Les fichiers de travail qu'on peut libérer, et ceux dont la perte coûterait des heures de narration, distingués.</sub>
</p>

---

## Installation

Une seule commande. Elle crée son propre environnement Python privé, détermine
quelle version de PyTorch convient à votre matériel, et propose d'installer ce
qui manque. Rien n'est installé au niveau du système et vous n'avez pas besoin
des droits d'administrateur.

**macOS et Linux**

```bash
curl -fsSL https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install-macos-linux.sh | bash
```

**Windows** — ouvrez PowerShell et lancez :

```powershell
irm https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install-windows.ps1 | iex
```

L'installateur parle français si votre système est en français ; sinon,
ajoutez `--lang fr` (macOS et Linux) ou `-Lang fr` (Windows).

Puis démarrez-la :

```bash
ebook-audiobook
```

Cela ouvre l'application dans sa propre fenêtre — pas de terminal à laisser
traîner, pas d'onglet perdu parmi les autres. Vous la trouverez aussi dans le
menu Démarrer sur Windows, le dossier Applications sur macOS, et le menu des
applications sur Linux.

**Fermer la fenêtre n'arrête pas une narration.** L'application continue de
tourner dans la barre des tâches, pour qu'une conversion lancée le soir se
termine toute seule ; rouvrez la fenêtre depuis l'icône ou en lançant
l'application une seconde fois. Pour l'arrêter vraiment, utilisez
**Quitter** — dans le menu de l'icône, ou en bas de la barre latérale. Dans les
deux cas elle vous prévient d'abord si une narration est en cours.

<details>
<summary>Ce qu'est vraiment la fenêtre, et quand l'icône n'est pas là</summary>

L'interface est une application web locale, affichée dans une fenêtre sans
bordure empruntée au navigateur de la famille Chromium que vous avez (Chrome,
Edge, Brave, Chromium). Si vous n'en avez aucun, elle se rabat sur un onglet
ordinaire de votre navigateur par défaut — Firefox n'a pas de mode fenêtre
équivalent.

L'icône a besoin d'une zone de notification, et **GNOME n'en a pas** sauf si
vous avez installé une extension AppIndicator. Sans icône, l'application
fonctionne quand même et continue de tourner après la fermeture de la fenêtre ;
vous la retrouvez en la relançant plutôt que depuis une icône, et vous quittez
depuis la barre latérale. Lancez avec `--no-tray` pour vous en passer
délibérément.

</details>

<details>
<summary>Options de l'installateur</summary>

| Option | Effet |
|---|---|
| `--cpu` / `-Cpu` | Forcer la version PyTorch pour processeur seul (~400 Mo au lieu de ~4 Go) |
| `--gpu` / `-Gpu` | Forcer la version CUDA quand la détection du GPU ne trouve rien (par ex. un `nvidia-smi` cassé) |
| `--rocm` / `--amd` | Forcer la version AMD ROCm (Linux + Radeon) |
| `--cuda128` / `-Cuda128` | Forcer la version CUDA 12.8 (RTX série 20 et plus récentes) |
| `--cuda126` / `-Cuda126` | Forcer la version CUDA 12.6 (GTX séries 900/1000) |
| `--no-tts` / `-NoTts` | Ne pas installer PyTorch pour l'instant — importer des livres, ajouter le moteur plus tard |
| `--lang fr` / `-Lang fr` | Messages de l'installateur en français |
| `--version X.Y.Z` | Installer une version précise |
| `--dir CHEMIN` / `-InstallDir` | Installer ailleurs que dans le dossier par défaut |
| `--yes` / `-Yes` | Accepter toutes les questions (installations scriptées) |
| `--uninstall` / `-Uninstall` | Retirer le programme, en gardant vos livres et réglages |

</details>

### Ce qu'il vous faut

| | |
|---|---|
| **Python 3.11+** | L'installateur propose de l'installer s'il manque |
| **[Calibre](https://calibre-ebook.com/download)** | Requis — c'est lui qui lit vos fichiers de livres. L'installateur propose de l'installer |
| **ffmpeg** | **Inclus.** Rien à faire |
| Un GPU | Facultatif. Voir [combien de temps ça prend](#combien-de-temps-ça-prend) |

### Désinstallation

```bash
ebook-audiobook-uninstall            # macOS/Linux
```
```powershell
iex "& { $(irm https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install-windows.ps1) } -Uninstall"
```

Vos livres, réglages et livres audio terminés ne sont **jamais** supprimés par
une désinstallation. `ebook-audiobook paths` vous montre où ils se trouvent.

---

## Utilisation

### La fenêtre de l'application

Lancez `ebook-audiobook`, ou ouvrez-la depuis votre menu d'applications. Puis :

1. **Ajouter un livre** → choisissez un livre numérique sans DRM sur votre ordinateur.
2. Choisissez une **voix** et ajustez les réglages — expressivité et rythme
   en premier, les réglages plus fins du moteur sous *Réglages du moteur*.
3. **Générez un aperçu** de n'importe quel chapitre. Il utilise exactement le
   même moteur et les mêmes réglages que la narration complète, donc il sonne
   comme le résultat final.
4. **Narrez le livre**, et choisissez où il va :
   - **Bibliothèque Plex** — rangé dans une arborescence `Auteur / Titre (Année) / Titre.m4b`
     prête pour Plex, avec un `cover.jpg` à côté. Définissez le dossier une fois dans les **Réglages**.
   - **Un dossier** — un dossier simple que vous choisissez.

   Dans les deux cas, l'accès en écriture à la destination est vérifié *avant*
   le début de la narration, pour qu'un mauvais chemin échoue en une seconde
   plutôt qu'après trois heures. Il y a un bouton **Arrêter**, et une narration
   interrompue reprend là où elle s'est arrêtée.
5. Le `.m4b` sort étiqueté pour Plex/Audnexus : marqué comme livre audio
   (`stik=2`), artiste de l'album réglé sur l'auteur, couverture intégrée, un
   marqueur par chapitre, plus l'année et l'ISBN quand le livre les fournit.
   ([Tous les lecteurs n'affichent pas les chapitres](#écouter-le-résultat) — ceux de Plex ne le font pas.)

**Voix** — ajoutez vos propres extraits de référence dont vous avez les droits,
écoutez-les, et changez de voix livre par livre.
**Bibliothèque** — chaque conversion faite, avec son état, sa taille et ses
actions de nettoyage.

### La ligne de commande

Les commandes et leurs messages restent en anglais ; l'interface graphique est
celle qui parle français.

```bash
ebook-audiobook                                   # ouvrir la fenêtre de l'application
ebook-audiobook web --no-tray                     # …sans icône dans la barre des tâches
ebook-audiobook web --no-browser                  # …serveur seul, sans fenêtre
ebook-audiobook check                             # tout est-il installé ?
ebook-audiobook paths                             # où sont mes données ?
ebook-audiobook languages                         # quelles langues de narration sont prêtes ?
ebook-audiobook languages install fr              # télécharger le modèle multilingue (~3 Go)
ebook-audiobook convert livre.epub --preview-seconds 30   # aperçu, puis confirmation
ebook-audiobook convert livre.epub -y --bitrate 64        # d'une traite
ebook-audiobook convert livre.epub --language fr          # narrer en français
ebook-audiobook convert livre.epub --voice-ref clip.wav   # cloner une voix
ebook-audiobook convert livre.epub --engine fake -y       # test de la chaîne sans GPU
ebook-audiobook list                              # conversions passées
ebook-audiobook backup ~/livres.zip               # sauvegarder votre travail
ebook-audiobook restore ~/livres.zip              # le remettre en place
ebook-audiobook update                            # y a-t-il une version plus récente ?
ebook-audiobook logs                              # ce qui a mal tourné récemment
ebook-audiobook report                            # la même chose, en rapport de bogue
```

---

## Langues

Deux choses distinctes, qui ne se mélangent pas :

**La langue de l'interface.** L'application suit la langue de votre navigateur.
Pour la fixer, ouvrez **Réglages → Langue** ; le choix est mémorisé, et le menu
de l'icône le suit aussi. Anglais et français aujourd'hui.

**La langue de la narration.** Elle appartient au livre, pas à vous : un livre
qui se déclare en français (ce que font presque tous les EPUB) est narré en
français, par l'une des voix françaises fournies, avec les nombres, les
abréviations et la typographie française lus comme un lecteur les lirait —
« 1625 » devient *mille six cent vingt-cinq*, « M. de Tréville » devient
*Monsieur de Tréville*, « 12,50 € » devient *douze euros et cinquante centimes*.
Vous pouvez changer la langue de narration sur la page du livre ; le livre est
alors relu et le narrateur de cette langue prend le relais.

Le français demande un second modèle vocal, d'environ 3 Go, téléchargé depuis
huggingface.co **uniquement** quand vous appuyez sur **Installer** dans
**Réglages → Langues de narration** (ou avec `ebook-audiobook languages install fr`).
Tant qu'il n'est pas là, un livre français est narré en anglais et la page du
livre vous dit ce qu'installer. Ce même modèle parle vingt et une autres
langues, proposées comme *expérimentales* : il les prononce, mais les nombres et
abréviations y sont lus tels quels.

<details>
<summary>Traduire l'interface</summary>

Les textes de l'interface sont dans
`ebook_audiobook/locale/fr/LC_MESSAGES/messages.po`, un fichier que
[Poedit](https://poedit.net/) (gratuit, Windows/macOS/Linux) ouvre directement :
l'anglais à gauche, le français à droite, avec un filtre « à revoir » pour les
entrées incertaines. Quelques règles : ne touchez pas à l'en-tête du fichier ;
laissez les marqueurs `%(nom)s` et les balises `<code>…</code>` exactement
comme en anglais ; utilisez l'espace insécable avant `:` `;` `?` `!` et à
l'intérieur des guillemets « ». Renvoyez le fichier `.po` tel quel — pas
besoin de git — et il sera compilé et vérifié avant d'être intégré.

</details>

---

## Sauvegarde

```bash
ebook-audiobook backup --dry-run          # ce qui serait inclus, et sa taille
ebook-audiobook backup ~/livres.zip       # par défaut : sans l'audio narré
```

Par défaut, l'audio narré est exclu, et la différence n'est pas subtile. Sur
une machine avec trois livres convertis :

```
+ settings                   1 files       113 B
+ voice clips                4 files      1.3 MB
+ imported books             5 files     10.4 MB
+ project data              23 files     19.0 MB
- rendered audio         4,637 files      3.3 GB  (excluded)
- finished audiobooks        3 files      5.0 MB  (excluded)

backup size (uncompressed): 30.8 MB in 33 files
left out:                   3.3 GB
```

L'audio narré est adressé par contenu et reproductible à partir du livre et de
vos réglages de voix : le garder coûte cent fois plus de place pour sauver
quelque chose qu'une nouvelle narration recrée à l'identique. Les 30 Mo sont
la partie qu'on ne peut pas recréer.

| Profil | Contenu |
|---|---|
| `--profile settings` | Réglages et extraits de voix. Minuscule. |
| `--profile projects` | **Par défaut.** Ce qui précède, plus vos livres et, pour chaque conversion, le découpage en chapitres, les métadonnées et les couvertures. |
| `--profile full` | Tout ce qui précède, plus l'audio narré et les `.m4b` terminés. |

Les options individuelles (`--include-audio`, `--no-imports`, `--include-outputs`,
`--include-models`) l'emportent sur le profil choisi, et `--max-size 500MB`
refuse d'écrire quoi que ce soit de plus gros. L'environnement Python du
programme n'est jamais inclus — il vit dans le dossier de données mais ce ne
sont pas vos données.

Une restauration n'écrase jamais un fichier existant sauf avec `--force`, pour
qu'elle ne puisse pas détruire en silence un travail plus récent que la sauvegarde.

---

## Mises à jour

```bash
ebook-audiobook update            # demander à GitHub quelle est la dernière version
ebook-audiobook update --apply    # télécharger et lancer l'installateur officiel
```

Rien ne vérifie les mises à jour tout seul. La vérification est une requête à
GitHub, et tout le principe de cette application est de ne parler à personne
sans qu'on le lui demande — cela n'arrive donc que lorsque vous lancez cette
commande ou appuyez sur le bouton dans les **Réglages**. La page des réglages
propose de vérifier à son ouverture ; c'est désactivé tant que vous ne
l'activez pas.

Mettre à jour relance le même installateur qu'un nouvel utilisateur, plutôt
qu'un chemin de mise à niveau à part, moins testé. Vos livres et réglages ne
sont pas touchés.

---

## Quand quelque chose ne va pas

Les échecs sont consignés dans un petit journal local — une ligne de JSON
chacun, limité à environ 750 Ko au total, et effacé après deux semaines. Rien
n'est transmis.

```bash
ebook-audiobook logs              # échecs récents
ebook-audiobook report            # un rapport de bogue en Markdown, prêt à envoyer
```

Le rapport inclut votre version, votre système, Python, le GPU et la trace de
l'erreur — de quoi diagnostiquer sans aller-retour. Avant affichage, votre
dossier personnel est remplacé par `~` et les titres des livres sont retirés,
pour que signaler un bogue ne publie pas votre historique de lecture.

---

## Écouter le résultat

Chaque livre est écrit avec un vrai marqueur par chapitre, stocké **deux fois** —
en piste de chapitres QuickTime et en atome Nero `chpl` — pour que tout lecteur
qui lit des chapitres les trouve. Une narration qui aurait perdu ses marqueurs
est refusée plutôt que livrée.

> **Plex Media Server ne lit pas les chapitres des fichiers audio.** Seulement
> ceux des vidéos. Dans Plex et Plexamp, votre livre apparaît comme une seule
> piste ininterrompue de dix heures, sans liste de chapitres ni boutons pour
> les sauter — ce qui rend la barre de lecture franchement dangereuse en
> voiture. C'est une limite de Plex, pas un problème du fichier ; c'est
> [une demande ouverte depuis 2018][plex-req] sans implémentation, et le
> mainteneur du principal guide Plex pour livres audio [dit la même chose][plex-guide].

Utilisez un lecteur qui lit lui-même les chapitres. Ceux-ci gardent Plex comme
bibliothèque, donc pas de second serveur à faire tourner :

| Plateforme | Lecteur | Notes |
|---|---|---|
| Android | [Chronicle Epilogue](https://play.google.com/store/apps/details?id=local.oss.chronicle) | Gratuit, [libre](https://github.com/mattttvaughn/chronicle). Chapitres, téléchargements hors ligne, Android Auto, vitesse 0,5–3×, minuterie de sommeil, progression synchronisée avec Plex. En bêta ouverte pour l'instant. Android 13+ ; la prise en charge d'Android Auto est basique (pas de commande vocale). |
| Android | [Bookcamp](https://play.google.com/store/apps/details?id=app.bookcamp.android) | Chapitres, hors ligne, Android Auto — mais sur abonnement seulement, et des avis signalent des ratés avec Android Auto et la lecture par chapitres. |
| iOS | [Prologue](https://prologue.audio/) | Chapitres, CarPlay (avec la liste des chapitres sur l'écran de lecture), Apple Watch, Siri, signets, minuterie, renforcement de la voix. Gratuit ; 5 $ une fois pour les téléchargements hors ligne. Parle aussi Audiobookshelf, donc survit à un changement ultérieur. |
| iOS | [Bookcamp](https://apps.apple.com/us/app/bookcamp/id1523540165) | Chapitres, hors ligne, synchronisation entre appareils — mais sur abonnement seulement. |

Pas attaché à Plex ? [Audiobookshelf](https://www.audiobookshelf.org/) lit ces
chapitres nativement, côté serveur comme côté client, et l'arborescence que cet
outil écrit (`{Auteur}/{Titre} (Année)/`, ou `{Auteur}/{Série}/{NN} - {Titre} (Année)/`)
correspond déjà à sa disposition `{Author}/{Series}/{Book}` — pointez-le sur le
même dossier et rien n'a besoin d'être renarré.

<details>
<summary>Vérifier les marqueurs vous-même</summary>

Si un lecteur n'affiche aucun chapitre, confirmez d'où vient le problème avant
d'accuser le fichier :

```bash
ffprobe -v error -print_format json -show_chapters "Votre livre.m4b" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["chapters"]))'
```

Un nombre supérieur à zéro signifie que les marqueurs sont là et que c'est le
lecteur qui les ignore.

</details>

[plex-req]: https://forums.plex.tv/t/support-for-reading-chapters-in-m4b-files/741874
[plex-guide]: https://github.com/seanap/Plex-Audiobook-Guide/discussions/92

---

## Combien de temps ça prend

Le modèle, les poids et la qualité audio sont **identiques sur tous les
appareils**. Seule la vitesse change.

| Votre matériel | Tourne sur | Un roman d'environ 110 000 mots prend |
|---|---|---|
| GPU NVIDIA, RTX série 20 et plus récentes | CUDA 12.8 | **2 à 3 heures** (~44 caractères/s sur une RTX 3070 Ti) |
| GPU NVIDIA, GTX séries 900/1000 | CUDA 12.6 | 2 à 4 heures |
| AMD Radeon, RX 9000 comprises (Linux) | ROCm 6.4 | 3 à 4 heures |
| Mac Apple Silicon, macOS 12.3+ | Metal | Quelques heures |
| macOS avant 12.3 | Processeur | De nombreuses heures |
| AMD Radeon (Windows) | Processeur | De nombreuses heures — les versions ROCm de PyTorch n'existent que sur Linux |
| Sans GPU (tout système) | Processeur | De nombreuses heures — ça marche, mais laissez tourner la nuit |
| **Mac Intel** | — | **Non pris en charge.** PyTorch ne compile plus pour les Mac Intel depuis la 2.2.2. L'application s'installe et tout fonctionne sauf la narration. |

L'installateur choisit tout seul la bonne version pour votre machine — CUDA,
ROCm, Metal ou processeur seul. `ebook-audiobook check` vous dit laquelle vous
avez et pourquoi. Le premier segment est plus lent (mise en route) ; générer un
aperçu mesure la vitesse réelle de votre machine et affine l'estimation
affichée pour le livre entier.

**NVIDIA** reçoit CUDA 12.8, ce qu'exigent les cartes RTX série 50 — CUDA 12.4
n'a aucun noyau pour elles. Les cartes plus anciennes que la RTX série 20
(GTX 900/1000) ne sont pas dans cette version, donc elles reçoivent CUDA 12.6 ;
l'installateur lit la capacité de calcul de votre carte et choisit. Sur une
machine à deux GPU il choisit pour le plus ancien, pour que les deux marchent.
`--cuda126` / `--cuda128` forcent le choix, et si le mauvais devait arriver,
`ebook-audiobook check` nomme l'option qui corrige, plutôt que de vous laisser
avec une erreur CUDA en pleine narration.

**AMD sur Linux** reçoit automatiquement la version ROCm quand une Radeon
dédiée est trouvée. Certaines cartes grand public (RX 6700/6600,
RX 7600/7700/7800) ne sont pas dans la liste prise en charge par ROCm et ont
besoin de `HSA_OVERRIDE_GFX_VERSION` pour être visibles — l'installateur le
détermine et l'inscrit dans le lanceur, donc rien à configurer. Les Radeon
intégrées restent volontairement sur la version processeur, plus rapide pour
elles que ROCm.

**Apple Silicon** est utilisé automatiquement — il n'y a qu'une version Mac de
PyTorch et elle inclut Metal, donc `--cpu` et `--gpu` ne changent rien à ce
qui est téléchargé. Metal demande macOS 12.3 ou plus récent ; en dessous, la
même installation tourne en silence sur le processeur, et l'installateur le
dit plutôt que de vous le laisser découvrir au temps de narration.

### Garder votre ordinateur utilisable

Une narration complète tourne à plein régime pendant des heures. Si vous
voulez continuer à travailler — ou si vous êtes sur un portable qui chauffe et
souffle — baissez le régime dans les **Réglages**, ou conversion par
conversion dans la fenêtre de lancement. Le livre audio est le même octet pour
octet ; seul le temps change.

| Mode | Ce qu'il fait | Coût |
|---|---|---|
| **Pleine vitesse** | Tout ce qui est disponible. Par défaut. | — |
| **Équilibré** | Limite les fils du processeur, baisse la priorité, courtes pauses | ~10 à 25 % plus lent |
| **Discret / arrière-plan** | Peu de fils, priorité minimale, pause la moitié du temps ; sur Apple Silicon, passe sur les **cœurs efficaces** | ~2× plus lent |

En ligne de commande : `--power quiet`. La vitesse mesurée en caractères par
seconde ignore les pauses, donc changer de mode ne fait pas paraître votre
matériel plus lent qu'il n'est.

**Si un GPU manque de mémoire** en cours de route, la narration ne meurt pas —
elle réessaie une fois, puis passe sur le processeur et termine. Tout ce qui
est déjà narré reste en cache dans les deux cas.

<details>
<summary>Variables d'environnement</summary>

| Variable | Effet |
|---|---|
| `EBAB_LANG` | Forcer la langue de l'interface (`en`, `fr`), avant le réglage et le navigateur |
| `EBAB_DEVICE` | Forcer `cuda`, `mps` ou `cpu` au lieu du choix automatique |
| `EBAB_DATA_ROOT` | Stocker toutes les données ailleurs que dans le dossier par défaut |
| `EBAB_VERBOSE=1` | Afficher les barres de progression et avertissements du moteur |
| `EBAB_PORT` / `EBAB_HOST` | Servir l'interface ailleurs que sur `127.0.0.1:5005` |
| `EBAB_NO_BROWSER=1` | Ne pas ouvrir de fenêtre au démarrage |
| `EBAB_EBOOK_CONVERT` | Chemin vers `ebook-convert` de Calibre, pour les installations inhabituelles |

</details>

---

## Comment ça marche

Une chaîne linéaire, adressée par contenu, reprenable :

```
livre → extraction → normalisation → découpage → narration  → assemblage → empaquetage → .m4b
        (Calibre)    (forme parlée,   (segments   (Chatterbox   (ffmpeg)     (ffmpeg,
                      par langue)      sûrs)       GPU/CPU/MPS)               chapitres+couverture)
```

L'identité de chaque segment est `hash(texte + réglages de voix + version du moteur)`.
Cette seule idée donne trois choses gratuitement : une narration interrompue
**reprend automatiquement**, un réglage de voix modifié **ne renarre que ce qui
a changé**, et changer quelque chose qui n'affecte pas l'audio (comme le
débit) ne renarre **rien**.

Tout sauf le moteur vocal est du Python pur et tourne sans GPU. Le moteur
`fake` fait passer toute la chaîne jusqu'à un vrai `.m4b` pour les tests.

---

## Où sont vos fichiers

Tout ce que l'application stocke tient dans un seul dossier — lancez
`ebook-audiobook paths` pour voir exactement où. Par défaut :

| Système | Emplacement |
|---|---|
| Windows | `%LOCALAPPDATA%\ebook-audiobook` |
| macOS | `~/Library/Application Support/ebook-audiobook` |
| Linux | `~/.local/share/ebook-audiobook` |

```
imports/          copies de vos livres source
jobs/             état de chaque livre + audio des segments et chapitres en cache
voices/           vos extraits de référence
outputs/          aperçus, et fichiers terminés quand aucun dossier de bibliothèque n'est défini
browser-profile/  le profil de navigateur de la fenêtre de l'application (~50 Mo, jetable)
settings.json
runtime.json      seulement pendant l'exécution : sur quel port l'application tourne
```

Les livres audio terminés vont dans votre **dossier de bibliothèque Plex**
(défini dans les Réglages), pas ici. Changez tout l'emplacement avec
`EBAB_DATA_ROOT=/un/chemin`. Les modèles vocaux vivent dans le cache
Hugging Face de votre compte (`~/.cache/huggingface/hub`), partagé avec tout
autre programme qui les utiliserait.

> Vous lancez depuis un dépôt source qui a déjà un dossier `local-data/` ? Il
> continue de fonctionner exactement comme avant — il a la priorité, pour
> qu'une installation existante ne soit jamais orpheline.

### Limiter l'espace disque

Narrer un livre laisse derrière lui l'audio brut de chaque phrase — plusieurs
gigaoctets par livre, et typiquement **environ 85 % de tout ce que
l'application stocke.** Il est gardé parce qu'il fait prendre des minutes au
lieu d'heures à une nouvelle narration après un changement de réglage. Une fois
que le livre sonne bien, c'est du poids mort.

Le chiffre vous suit donc partout. La barre latérale affiche sur chaque écran
un total de ce qui peut partir sans risque, et **Stockage** le détaille livre
par livre :

```
7,5 Go   fichiers de travail libérables
0,8 Go   fichiers de travail permettant une reprise
1,4 Go   livres audio terminés
 48 Mo   vos livres, vos voix et vos choix
```

La distinction entre les deux premières lignes est celle qui compte. Pour un
livre **terminé**, ces fichiers n'apportent rien qu'on puisse entendre, donc
ils sont cochés par défaut. Pour un livre dont la narration s'est **arrêtée en
cours**, ils *sont* la reprise — les supprimer signifie narrer ces chapitres à
nouveau — donc ils sont retenus, décochés, et la ligne vous dit ce que ça
coûterait.

Ou arrêtez d'y penser : activez **Les libérer dès qu'un livre est terminé**
(dans Stockage ou dans les Réglages) et chaque livre fait le ménage derrière lui
dès que son `.m4b` est écrit. C'est désactivé par défaut — rien ici ne supprime
quoi que ce soit que vous n'ayez pas demandé.

Quoi que vous libériez, vous gardez le livre audio terminé, votre livre, vos
voix et vos choix de sections, et vous pouvez toujours narrer le livre à
nouveau depuis le début. **Supprimer** est l'autre action, et elle retire toute
la conversion, `.m4b` compris. Les aperçus ne s'accumulent pas : il n'y en a
jamais qu'un par livre, effacé automatiquement quand une narration complète se
termine. Rien ne peut être libéré pendant qu'un livre est narré.

---

## Dépannage

**« Calibre n'est pas installé »** — installez-le depuis
[calibre-ebook.com](https://calibre-ebook.com/download), ou
`winget install calibre.calibre` / `brew install --cask calibre` /
`sudo apt install calibre`. Sur macOS vous n'avez **pas** besoin de l'ajouter à
votre PATH ; l'application regarde elle-même dans `/Applications/calibre.app`.

**La commande `ebook-audiobook` est introuvable** — sur macOS/Linux, ajoutez
`~/.local/bin` à votre PATH (l'installateur vous donne la ligne exacte). Sur
Windows, ouvrez un **nouveau** terminal — les changements de PATH n'affectent
pas ceux déjà ouverts.

**« Ce livre semble protégé par DRM »** — cet outil ne retire pas les DRM et
n'ouvre pas les fichiers protégés. Apportez une copie sans DRM.

**Un PDF scanné ne produit rien** — un PDF composé d'images n'a aucun texte à
narrer. Il vous faut une version passée par la reconnaissance de caractères, ou un EPUB.

**Les narrations sont très lentes** — consultez `ebook-audiobook check`. S'il
indique `device=cpu` alors que vous avez une carte NVIDIA, c'est la version
PyTorch pour processeur qui a été installée ; relancez l'installateur pour
obtenir la version CUDA.

**Un livre français est narré en anglais** — le modèle multilingue n'est pas
installé, ou le livre ne déclare pas sa langue. Installez-le dans
**Réglages → Langues de narration**, puis choisissez *français* dans la liste
**Langue de la narration** sur la page du livre.

Pour tout le reste, lancez d'abord `ebook-audiobook check` — il rapporte
l'état de chaque prérequis et comment corriger ce qui manque.

---

## Usage responsable et vie privée

Cet outil est fait pour **les livres numériques que vous possédez et que vous
avez le droit de convertir**. Il ne retire **pas** les DRM et n'ouvrira pas de
fichiers protégés. Les livres audio générés sont pour un **usage personnel** ;
convertir et redistribuer une œuvre protégée relève de vous, pas de cet outil.

Le clonage de voix depuis un extrait de référence est facultatif et local
uniquement. Utilisez une voix dont vous avez les **droits et le consentement**
— la vôtre, ou un extrait libre de droits. Ne clonez pas la voix de quelqu'un
sans sa permission.

**Filigrane :** Chatterbox insère un filigrane inaudible
[Resemble Perth](https://github.com/resemble-ai/chatterbox) dans tout l'audio
généré, pour qu'une parole produite par IA puisse être identifiée après coup.
C'est une fonction délibérée d'IA responsable, présente dans chaque fichier
produit.

**Vie privée :** tout tourne en local. Pas d'API dans le cloud et pas de
télémétrie. Vos livres, vos extraits de voix et l'audio généré ne quittent
jamais la machine. Le seul trafic réseau que cette application fait jamais, en
entier :

| Quoi | Quand |
|---|---|
| Télécharger l'application | Installation et mise à jour |
| Le modèle vocal anglais (~3 Go) depuis Hugging Face | Votre première narration |
| Le modèle multilingue (~3 Go) depuis Hugging Face | Seulement quand vous appuyez sur **Installer** dans Réglages → Langues de narration |
| Demander à GitHub le numéro de la dernière version | Seulement quand vous lancez `ebook-audiobook update` ou appuyez sur **Rechercher des mises à jour** |

La vérification de version est désactivée par défaut et n'a jamais lieu sur
minuterie ni au démarrage. Activer « vérifier à l'ouverture de cette page »
dans les Réglages est la seule façon qu'elle ait lieu sans que vous appuyiez
sur quelque chose, et c'est facultatif. Rien vous concernant ni concernant
votre bibliothèque n'est envoyé — c'est une demande de numéro de version.

Le journal des échecs est local : il est écrit dans votre dossier de données,
limité en taille, effacé après deux semaines, et jamais transmis.
`ebook-audiobook report` l'affiche pour que *vous* le partagiez si vous le
souhaitez, avec votre dossier personnel et les titres des livres retirés.

**Sécurité :** l'interface derrière la fenêtre de l'application n'a **aucune
authentification** et n'écoute que sur `127.0.0.1`, délibérément. C'est un
outil local mono-utilisateur. Ne l'exposez pas à un réseau et ne l'attachez pas
à `0.0.0.0` — la fonction « importer par chemin local » lit des fichiers
locaux arbitraires, donc une instance exposée les divulguerait.

---

## Contribuer / lancer depuis les sources

Voir [CONTRIBUTING.md](CONTRIBUTING.md) (en anglais) pour l'environnement de
développement, la suite de tests, et comment les versions sont publiées.

## Licence

[MIT](LICENSE) — libre d'utilisation, de modification et de distribution.
Chatterbox est également sous MIT. Le binaire ffmpeg embarqué (via
`imageio-ffmpeg`) est sous licence GPL par ses propres auteurs ; il est invoqué
comme un programme séparé, pas lié à ce code.
