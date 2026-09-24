<#
.SYNOPSIS
  ebook-audiobook installer for Windows.

.DESCRIPTION
  Run this in PowerShell:

    irm https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install-windows.ps1 | iex

  What it does, in order:
    1. finds a 64-bit Python 3.11+ (offers to install one, via winget or
       from python.org)
    2. creates a private virtualenv under %LOCALAPPDATA%\ebook-audiobook
    3. installs the app, plus the right PyTorch build for this machine
    4. checks for Calibre and offers to install it (the one step that asks
       Windows for permission, because Calibre installs for every user)
    5. adds an `ebook-audiobook` command and a Start Menu shortcut

  Everything else is per-user: nothing of this program is written outside your
  own profile, and your books and settings are never touched by an
  upgrade or an uninstall.

.PARAMETER Version
  Install a specific release instead of the latest.

.PARAMETER Cpu
  Force the CPU-only PyTorch build (a much smaller download).

.PARAMETER Gpu
  Force the CUDA PyTorch build, even if this script's GPU probe came up empty
  (for example, a broken nvidia-smi).

.PARAMETER Cuda126
  Force the CUDA 12.6 PyTorch build, for GTX 900/1000-series and older cards
  that the newer build has no kernels for.

.PARAMETER Cuda128
  Force the CUDA 12.8 PyTorch build (RTX 20-series and newer).

.PARAMETER NoTts
  Skip PyTorch entirely. You can import books but not render audio yet.

.PARAMETER Yes
  Accept all prompts, for scripted installs.

.PARAMETER Uninstall
  Remove the program. Your books and settings are kept.
#>
[CmdletBinding()]
param(
    [string]$Version = "latest",
    [string]$InstallDir = "",
    [switch]$Cpu,
    [switch]$Gpu,
    [switch]$Cuda126,
    [switch]$Cuda128,
    [switch]$NoTts,
    [switch]$Yes,
    [switch]$Uninstall,
    [string]$Lang = ""
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 redraws its progress bar for every chunk a download
# receives, which makes Invoke-WebRequest many times slower than the network.
$ProgressPreference = "SilentlyContinue"
# ...and on an older .NET it may not offer TLS 1.2 unless asked, which GitHub
# and python.org both require.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }
$Repo = "denelson1-dot/ebook-audiobook"
# The release workflow rewrites this line in the published copy of this script,
# so the installer always knows exactly which wheel it belongs to. A wheel's
# filename must contain its version to be installable, so a fixed
# "latest/download/..." asset name is not an option; baking the version in beats
# calling the GitHub API, which is rate-limited for unauthenticated users.
$PinnedVersion = "__EBAB_VERSION__"

# --- language ----------------------------------------------------------------
# -Lang, then EBAB_LANG, then Windows' own display language. The helpers pass
# every message through Tr, which matches the English text (exactly, or with a
# wildcard for lines that carry a value) and returns the translation. Call
# sites stay English, so the logic is the same in every language.
if (-not $Lang) { $Lang = $env:EBAB_LANG }
if (-not $Lang) { try { $Lang = (Get-Culture).TwoLetterISOLanguageName } catch { $Lang = "en" } }
$Lang = switch -Wildcard ("$Lang".ToLower()) { "fr*" { "fr" } "es*" { "es" } "ja*" { "ja" } default { "en" } }
# Windows PowerShell 5.1 reads this BOM-less file as the ANSI code page, so
# every non-ASCII literal in it is mangled before the console ever sees it.
# French and Spanish lose their accents and stay readable; Japanese is
# non-ASCII from end to end and would be nothing but replacement characters,
# which is strictly worse than English. So 5.1 gets English for Japanese.
if ($Lang -eq "ja" -and $PSVersionTable.PSVersion.Major -lt 6) { $Lang = "en" }
# Helps the console render what we do print, on both editions.
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

# UTF-8 without a byte-order mark, deliberately: PowerShell 7 reads that as
# UTF-8, and a mark at the start of the text `irm | iex` hands to the parser
# would be a syntax error there. Windows PowerShell 5.1 shows the accents in
# these French lines as mojibake instead; the English, and the logic, are
# unaffected either way.
$French = [ordered]@{
    "Uninstalling ebook-audiobook" = "Désinstallation d'ebook-audiobook"
    "program removed" = "programme retiré"
    "  Your books, settings, and audiobooks were NOT deleted. They're in:" = "  Vos livres, réglages et livres audio n'ont PAS été supprimés. Ils sont dans :"
    "  Delete that folder yourself if you want them gone." = "  Supprimez ce dossier vous-même si vous voulez vous en débarrasser."
    "ebook-audiobook installer" = "Installateur d'ebook-audiobook"
    "Turns ebooks you own into narrated audiobooks, entirely offline." = "Transforme vos livres numériques en livres audio narrés, entièrement hors ligne."
    "Looking for Python 3.11 or newer" = "Recherche de Python 3.11 ou plus récent"
    "no Python 3.11+ found" = "aucun Python 3.11+ trouvé"
    "Install Python 3.12 now with winget?" = "Installer Python 3.12 maintenant avec winget ?"
    "Creating a private environment" = "Création d'un environnement privé"
    "reusing the existing environment (upgrading in place)" = "réutilisation de l'environnement existant (mise à niveau sur place)"
    "created" = "créé"
    "Installing ebook-audiobook" = "Installation d'ebook-audiobook"
    "installed *" = "installé : *"
    "Skipping the speech engine (-NoTts)" = "Moteur vocal ignoré (-NoTts)"
    "you can import books, but rendering audio needs the engine" = "vous pouvez importer des livres, mais produire l'audio demande le moteur"
    "Setting up the speech engine" = "Mise en place du moteur vocal"
    "couldn't ask the app which PyTorch build to use; falling back to CPU-only" = "impossible de demander à l'application quelle version de PyTorch utiliser ; repli sur la version processeur"
    "Download and install the speech engine now?" = "Télécharger et installer le moteur vocal maintenant ?"
    "speech engine ready*" = "moteur vocal prêt*"
    "The ~3 GB voice model downloads the first time you render." = "Le modèle vocal (~3 Go) se télécharge à la première narration."
    "skipped - re-run this installer to add it later." = "ignoré - relancez cet installateur pour l'ajouter plus tard."
    "Checking for Calibre (needed to read ebook files)" = "Recherche de Calibre (nécessaire pour lire les fichiers de livres)"
    "Calibre found" = "Calibre trouvé"
    "Calibre is not installed" = "Calibre n'est pas installé"
    "Install it now with 'winget install calibre.calibre'?" = "L'installer maintenant avec « winget install calibre.calibre » ?"
    "winget install failed" = "l'installation par winget a échoué"
    "Calibre installed" = "Calibre installé"
    "install Calibre before converting a book:" = "installez Calibre avant de convertir un livre :"
    "or download it from https://calibre-ebook.com/download" = "ou téléchargez-le depuis https://calibre-ebook.com/download"
    "Creating the launcher" = "Création du lanceur"
    "command: *" = "commande : *"
    "added to your PATH (new terminals will find it)" = "ajouté à votre PATH (les nouveaux terminaux le trouveront)"
    "Start Menu shortcut" = "raccourci dans le menu Démarrer"
    "Add a Desktop shortcut too?" = "Ajouter aussi un raccourci sur le Bureau ?"
    "Desktop shortcut" = "raccourci sur le Bureau"
    "couldn't create shortcuts: *" = "impossible de créer les raccourcis : *"
    "Verifying the install" = "Vérification de l'installation"
    "all required components are working" = "tous les composants requis fonctionnent"
    "some checks failed - run 'ebook-audiobook check' for details" = "certaines vérifications ont échoué - lancez « ebook-audiobook check » pour les détails"
    "Installed." = "Installé."
    "  Start it from the Start Menu, or run: " = "  Démarrez-la depuis le menu Démarrer, ou lancez : "
    "  Your books live in: " = "  Vos livres sont dans : "
    "  Check setup:  " = "  Vérifier :     "
    "  Uninstall:    " = "  Désinstaller : "
    "  Note: open a NEW terminal for the 'ebook-audiobook' command to be found." = "  Note : ouvrez un NOUVEAU terminal pour que la commande « ebook-audiobook » soit trouvée."
    "ebook-audiobook is running, and has to close first" = "ebook-audiobook est ouvert, et doit d'abord être fermé"
    "Close it now? A render in progress stops, and picks up where it left off when you start it again." = "Le fermer maintenant ? Une narration en cours s'arrête, et reprend là où elle en était quand vous la relancez."
    "quit ebook-audiobook from its tray icon, then run this again." = "quittez ebook-audiobook depuis son icône près de l'horloge, puis relancez ceci."
    "(it must be 64-bit x64 Python - PyTorch has no 32-bit or ARM64 Windows build)" = "(il faut un Python 64 bits x64 - PyTorch n'existe pas pour Windows 32 bits ni ARM64)"
    "Download Python 3.12 from python.org and install it, just for you?" = "Télécharger Python 3.12 depuis python.org et l'installer, pour vous seul ?"
    "couldn't download or run the Python installer: *" = "impossible de télécharger ou de lancer l'installateur de Python : *"
    "Download Calibre (about 220 MB) from calibre-ebook.com and install it?" = "Télécharger Calibre (environ 220 Mo) depuis calibre-ebook.com et l'installer ?"
    "Windows will ask for permission to install it. If nothing appears, look for a flashing icon on the taskbar." = "Windows va demander l'autorisation de l'installer. Si rien n'apparaît, cherchez une icône qui clignote dans la barre des tâches."
    "couldn't download or run the Calibre installer: *" = "impossible de télécharger ou de lancer l'installateur de Calibre : *"
    "This can take a few minutes, with nothing to show until it's done." = "Cela peut prendre quelques minutes, sans rien afficher avant la fin."
}

$Spanish = [ordered]@{
    "Uninstalling ebook-audiobook" = "Desinstalando ebook-audiobook"
    "program removed" = "programa eliminado"
    "  Your books, settings, and audiobooks were NOT deleted. They're in:" = "  Tus libros, ajustes y audiolibros NO se han borrado. Están en:"
    "  Delete that folder yourself if you want them gone." = "  Borra tú mismo esa carpeta si quieres deshacerte de ellos."
    "ebook-audiobook installer" = "Instalador de ebook-audiobook"
    "Turns ebooks you own into narrated audiobooks, entirely offline." = "Convierte los libros que son tuyos en audiolibros narrados, del todo sin conexión."
    "Looking for Python 3.11 or newer" = "Buscando Python 3.11 o superior"
    "no Python 3.11+ found" = "no se encontró Python 3.11 o superior"
    "Install Python 3.12 now with winget?" = "¿Instalar ahora Python 3.12 con winget?"
    "Creating a private environment" = "Creando un entorno privado"
    "reusing the existing environment (upgrading in place)" = "reutilizando el entorno existente (actualizándolo en el sitio)"
    "created" = "creado"
    "Installing ebook-audiobook" = "Instalando ebook-audiobook"
    "installed *" = "instalado *"
    "Skipping the speech engine (-NoTts)" = "Se omite el motor de voz (-NoTts)"
    "you can import books, but rendering audio needs the engine" = "puedes importar libros, pero generar audio necesita el motor"
    "Setting up the speech engine" = "Preparando el motor de voz"
    "couldn't ask the app which PyTorch build to use; falling back to CPU-only" = "no se pudo preguntar a la aplicación qué versión de PyTorch usar; se recurre a la de solo procesador"
    "Download and install the speech engine now?" = "¿Descargar e instalar ahora el motor de voz?"
    "speech engine ready*" = "motor de voz listo*"
    "The ~3 GB voice model downloads the first time you render." = "El modelo de voz (~3 GB) se descarga la primera vez que generes audio."
    "skipped - re-run this installer to add it later." = "omitido - vuelve a ejecutar este instalador para añadirlo más tarde."
    "Checking for Calibre (needed to read ebook files)" = "Buscando Calibre (necesario para leer los archivos de libros)"
    "Calibre found" = "Calibre encontrado"
    "Calibre is not installed" = "Calibre no está instalado"
    "Install it now with 'winget install calibre.calibre'?" = "¿Instalarlo ahora con 'winget install calibre.calibre'?"
    "winget install failed" = "winget no pudo instalarlo"
    "Calibre installed" = "Calibre instalado"
    "install Calibre before converting a book:" = "instala Calibre antes de convertir un libro:"
    "or download it from https://calibre-ebook.com/download" = "o descárgalo desde https://calibre-ebook.com/download"
    "Creating the launcher" = "Creando el lanzador"
    "command: *" = "comando: *"
    "added to your PATH (new terminals will find it)" = "añadido a tu PATH (los terminales nuevos lo encontrarán)"
    "Start Menu shortcut" = "acceso directo en el menú Inicio"
    "Add a Desktop shortcut too?" = "¿Añadir también un acceso directo en el escritorio?"
    "Desktop shortcut" = "acceso directo en el escritorio"
    "couldn't create shortcuts: *" = "no se pudieron crear los accesos directos: *"
    "Verifying the install" = "Verificando la instalación"
    "all required components are working" = "todos los componentes necesarios funcionan"
    "some checks failed - run 'ebook-audiobook check' for details" = "algunas comprobaciones fallaron - ejecuta 'ebook-audiobook check' para ver los detalles"
    "Installed." = "Instalado."
    "  Start it from the Start Menu, or run: " = "  Iníciala desde el menú Inicio, o ejecuta: "
    "  Your books live in: " = "  Tus libros están en: "
    "  Check setup:  " = "  Comprobar:  "
    "  Uninstall:    " = "  Desinstalar:    "
    "  Note: open a NEW terminal for the 'ebook-audiobook' command to be found." = "  Nota: abre un terminal NUEVO para que se encuentre el comando 'ebook-audiobook'."
    "ebook-audiobook is running, and has to close first" = "ebook-audiobook está abierto y tiene que cerrarse primero"
    "Close it now? A render in progress stops, and picks up where it left off when you start it again." = "¿Cerrarlo ahora? Si está generando audio, se detiene y sigue donde se quedó cuando lo vuelvas a iniciar."
    "quit ebook-audiobook from its tray icon, then run this again." = "cierra ebook-audiobook desde su icono junto al reloj y vuelve a ejecutar esto."
    "(it must be 64-bit x64 Python - PyTorch has no 32-bit or ARM64 Windows build)" = "(tiene que ser Python de 64 bits x64 - PyTorch no existe para Windows de 32 bits ni ARM64)"
    "Download Python 3.12 from python.org and install it, just for you?" = "¿Descargar Python 3.12 de python.org e instalarlo solo para ti?"
    "couldn't download or run the Python installer: *" = "no se pudo descargar o ejecutar el instalador de Python: *"
    "Download Calibre (about 220 MB) from calibre-ebook.com and install it?" = "¿Descargar Calibre (unos 220 MB) de calibre-ebook.com e instalarlo?"
    "Windows will ask for permission to install it. If nothing appears, look for a flashing icon on the taskbar." = "Windows pedirá permiso para instalarlo. Si no aparece nada, busca un icono que parpadee en la barra de tareas."
    "couldn't download or run the Calibre installer: *" = "no se pudo descargar o ejecutar el instalador de Calibre: *"
    "This can take a few minutes, with nothing to show until it's done." = "Puede tardar unos minutos, sin mostrar nada hasta que termine."
}

# Japanese, base64-encoded — deliberately, and this is not decoration.
#
# This file has no byte-order mark, because the text `irm | iex` hands to the
# parser cannot start with one. Windows PowerShell 5.1 therefore decodes it
# with the system ANSI code page, and several kana have UTF-8 bytes that land
# on the CP125x "smart quote" code points (U+201C-201F), which the tokenizer
# accepts as string delimiters. A literal Japanese table ends the string
# mid-line and the parse cascades: install.ps1 would not run at all on the
# default Windows shell, in ANY language, not merely fail to translate.
#
# Base64 keeps this table pure ASCII, so the file parses under every code
# page, and the text is exact at runtime under both editions. French and
# Spanish need no such treatment: their accents are C2/C3 + A0-BF, which
# never collide with a delimiter.
$JapaneseB64 = [ordered]@{
    "Uninstalling ebook-audiobook" = "ZWJvb2stYXVkaW9ib29rIOOCkuOCouODs+OCpOODs+OCueODiOODvOODq+OBl+OBpuOBhOOBvuOBmQ=="
    "program removed" = "44OX44Ot44Kw44Op44Og44KS5YmK6Zmk44GX44G+44GX44Gf"
    "  Your books, settings, and audiobooks were NOT deleted. They're in:" = "ICDmnKzjgIHoqK3lrprjgIHjgqrjg7zjg4fjgqPjgqrjg5bjg4Pjgq/jga/liYrpmaTjgZXjgozjgabjgYTjgb7jgZvjgpPjgILloLTmiYA6"
    "  Delete that folder yourself if you want them gone." = "ICDkuI3opoHjgafjgYLjgozjgbDjgIHjgZ3jga7jg5Xjgqnjg6vjg4Djg7zjga/jgZToh6rliIbjgafliYrpmaTjgZfjgabjgY/jgaDjgZXjgYTjgII="
    "ebook-audiobook installer" = "ZWJvb2stYXVkaW9ib29rIOOCpOODs+OCueODiOODvOODqeODvA=="
    "Turns ebooks you own into narrated audiobooks, entirely offline." = "44GK5omL5oyB44Gh44Gu6Zu75a2Q5pu457GN44KS44CB5a6M5YWo44Gr44Kq44OV44Op44Kk44Oz44Gn44Kq44O844OH44Kj44Kq44OW44OD44Kv44Gr44GX44G+44GZ44CC"
    "Looking for Python 3.11 or newer" = "UHl0aG9uIDMuMTEg5Lul5LiK44KS5o6i44GX44Gm44GE44G+44GZ"
    "no Python 3.11+ found" = "UHl0aG9uIDMuMTEg5Lul5LiK44GM6KaL44Gk44GL44KK44G+44Gb44KT"
    "Install Python 3.12 now with winget?" = "d2luZ2V0IOOBpyBQeXRob24gMy4xMiDjgpLku4rjgZnjgZDjgqTjg7Pjgrnjg4jjg7zjg6vjgZfjgb7jgZnjgYvvvJ8="
    "Creating a private environment" = "5bCC55So44Gu55Kw5aKD44KS5L2c5oiQ44GX44Gm44GE44G+44GZ"
    "reusing the existing environment (upgrading in place)" = "5pei5a2Y44Gu55Kw5aKD44KS5YaN5Yip55So44GX44G+44GZ77yI44Gd44Gu5aC044Gn5pu05paw44GX44G+44GZ77yJ"
    "created" = "5L2c5oiQ44GX44G+44GX44Gf"
    "Installing ebook-audiobook" = "ZWJvb2stYXVkaW9ib29rIOOCkuOCpOODs+OCueODiOODvOODq+OBl+OBpuOBhOOBvuOBmQ=="
    "installed *" = "44Kk44Oz44K544OI44O844Or5riI44G/ICo="
    "Skipping the speech engine (-NoTts)" = "6Z+z5aOw44Ko44Oz44K444Oz44KS44K544Kt44OD44OX44GX44G+44GZ77yILU5vVHRz77yJ"
    "you can import books, but rendering audio needs the engine" = "5pys44Gu5Y+W44KK6L6844G/44Gv44Gn44GN44G+44GZ44GM44CB6Z+z5aOw44Gu55Sf5oiQ44Gr44Gv44Ko44Oz44K444Oz44GM5b+F6KaB44Gn44GZ"
    "Setting up the speech engine" = "6Z+z5aOw44Ko44Oz44K444Oz44KS5rqW5YKZ44GX44Gm44GE44G+44GZ"
    "couldn't ask the app which PyTorch build to use; falling back to CPU-only" = "44Gp44GuIFB5VG9yY2gg44KS5L2/44GG44GL44Ki44OX44Oq44Gr5ZWP44GE5ZCI44KP44Gb44KJ44KM44G+44Gb44KT44Gn44GX44Gf44CCQ1BVIOeJiOOBp+e2muihjOOBl+OBvuOBmQ=="
    "Download and install the speech engine now?" = "6Z+z5aOw44Ko44Oz44K444Oz44KS5LuK44GZ44GQ44OA44Km44Oz44Ot44O844OJ44GX44Gm44Kk44Oz44K544OI44O844Or44GX44G+44GZ44GL77yf"
    "speech engine ready*" = "6Z+z5aOw44Ko44Oz44K444Oz44Gu5rqW5YKZ44GM44Gn44GN44G+44GX44GfKg=="
    "The ~3 GB voice model downloads the first time you render." = "6Z+z5aOw44Oi44OH44Or77yI57SEIDMgR0LvvInjga/jgIHmnIDliJ3jgavnlJ/miJDjgZnjgovjgajjgY3jgavjg4Djgqbjg7Pjg63jg7zjg4njgZXjgozjgb7jgZnjgII="
    "skipped - re-run this installer to add it later." = "44K544Kt44OD44OX44GX44G+44GX44GfIC0g44GC44Go44Gn6L+95Yqg44GZ44KL44Gr44Gv44GT44Gu44Kk44Oz44K544OI44O844Op44O844KS5YaN5a6f6KGM44GX44Gm44GP44Gg44GV44GE44CC"
    "Checking for Calibre (needed to read ebook files)" = "Q2FsaWJyZSDjgpLnorroqo3jgZfjgabjgYTjgb7jgZnvvIjpm7vlrZDmm7jnsY3jga7oqq3jgb/ovrzjgb/jgavlv4XopoHjgafjgZnvvIk="
    "Calibre found" = "Q2FsaWJyZSDjgYzopovjgaTjgYvjgorjgb7jgZfjgZ8="
    "Calibre is not installed" = "Q2FsaWJyZSDjgYzjgqTjg7Pjgrnjg4jjg7zjg6vjgZXjgozjgabjgYTjgb7jgZvjgpM="
    "Install it now with 'winget install calibre.calibre'?" = "J3dpbmdldCBpbnN0YWxsIGNhbGlicmUuY2FsaWJyZScg44Gn5LuK44GZ44GQ44Kk44Oz44K544OI44O844Or44GX44G+44GZ44GL77yf"
    "winget install failed" = "d2luZ2V0IOOBq+OCiOOCi+OCpOODs+OCueODiOODvOODq+OBq+WkseaVl+OBl+OBvuOBl+OBnw=="
    "Calibre installed" = "Q2FsaWJyZSDjgpLjgqTjg7Pjgrnjg4jjg7zjg6vjgZfjgb7jgZfjgZ8="
    "install Calibre before converting a book:" = "5pys44KS5aSJ5o+b44GZ44KL5YmN44GrIENhbGlicmUg44KS44Kk44Oz44K544OI44O844Or44GX44Gm44GP44Gg44GV44GEOg=="
    "or download it from https://calibre-ebook.com/download" = "44G+44Gf44GvIGh0dHBzOi8vY2FsaWJyZS1lYm9vay5jb20vZG93bmxvYWQg44GL44KJ44OA44Km44Oz44Ot44O844OJ44GX44Gm44GP44Gg44GV44GE"
    "Creating the launcher" = "44Op44Oz44OB44Oj44O844KS5L2c5oiQ44GX44Gm44GE44G+44GZ"
    "command: *" = "44Kz44Oe44Oz44OJOiAq"
    "added to your PATH (new terminals will find it)" = "UEFUSCDjgavov73liqDjgZfjgb7jgZfjgZ/vvIjmlrDjgZfjgYTjgr/jg7zjg5/jg4rjg6vjgafopovjgaTjgYvjgorjgb7jgZnvvIk="
    "Start Menu shortcut" = "44K544K/44O844OI44Oh44OL44Ol44O844Gu44K344On44O844OI44Kr44OD44OI"
    "Add a Desktop shortcut too?" = "44OH44K544Kv44OI44OD44OX44Gr44KC44K344On44O844OI44Kr44OD44OI44KS5L2c5oiQ44GX44G+44GZ44GL77yf"
    "Desktop shortcut" = "44OH44K544Kv44OI44OD44OX44Gu44K344On44O844OI44Kr44OD44OI"
    "couldn't create shortcuts: *" = "44K344On44O844OI44Kr44OD44OI44KS5L2c5oiQ44Gn44GN44G+44Gb44KT44Gn44GX44GfOiAq"
    "Verifying the install" = "44Kk44Oz44K544OI44O844Or44KS5qSc6Ki844GX44Gm44GE44G+44GZ"
    "all required components are working" = "5b+F6KaB44Gq5qeL5oiQ6KaB57Sg44Gv44GZ44G544Gm5YuV5L2c44GX44Gm44GE44G+44GZ"
    "some checks failed - run 'ebook-audiobook check' for details" = "5LiA6YOo44Gu56K66KqN44Gr5aSx5pWX44GX44G+44GX44GfIC0g6Kmz44GX44GP44GvICdlYm9vay1hdWRpb2Jvb2sgY2hlY2snIOOCkuWun+ihjOOBl+OBpuOBj+OBoOOBleOBhA=="
    "Installed." = "44Kk44Oz44K544OI44O844Or44GM5a6M5LqG44GX44G+44GX44Gf44CC"
    "  Start it from the Start Menu, or run: " = "ICDjgrnjgr/jg7zjg4jjg6Hjg4vjg6Xjg7zjgYvjgonotbfli5XjgZnjgovjgYvjgIHmrKHjgpLlrp/ooYzjgZfjgabjgY/jgaDjgZXjgYQ6IA=="
    "  Your books live in: " = "ICDmnKzjga7loLTmiYA6IA=="
    "  Check setup:  " = "ICDoqK3lrprjga7norroqo06ICA="
    "  Uninstall:    " = "ICDjgqLjg7PjgqTjg7Pjgrnjg4jjg7zjg6s6ICAgIA=="
    "  Note: open a NEW terminal for the 'ebook-audiobook' command to be found." = "ICDms6jmhI86ICdlYm9vay1hdWRpb2Jvb2snIOOCs+ODnuODs+ODieOCkuS9v+OBhuOBq+OBr+OAgeaWsOOBl+OBhOOCv+ODvOODn+ODiuODq+OCkumWi+OBhOOBpuOBj+OBoOOBleOBhOOAgg=="
    "ebook-audiobook is running, and has to close first" = "ZWJvb2stYXVkaW9ib29rIOOBjOi1t+WLleOBl+OBpuOBhOOBvuOBmeOAguWFiOOBq+e1guS6huOBmeOCi+W/heimgeOBjOOBguOCiuOBvuOBmQ=="
    "Close it now? A render in progress stops, and picks up where it left off when you start it again." = "5LuK44GZ44GQ57WC5LqG44GX44G+44GZ44GL77yf55Sf5oiQ5Lit44Gu6Z+z5aOw44Gv5Lit5pat44GV44KM44CB5qyh44Gr6ZaL5aeL44GX44Gf44Go44GN44Gr57aa44GN44GL44KJ5YaN6ZaL44GX44G+44GZ44CC"
    "quit ebook-audiobook from its tray icon, then run this again." = "5pmC6KiI44Gu6L+R44GP44Gr44GC44KL44OI44Os44Kk44Ki44Kk44Kz44Oz44GL44KJIGVib29rLWF1ZGlvYm9vayDjgpLntYLkuobjgZfjgabjgYvjgonjgIHjgoLjgYbkuIDluqblrp/ooYzjgZfjgabjgY/jgaDjgZXjgYTjgII="
    "(it must be 64-bit x64 Python - PyTorch has no 32-bit or ARM64 Windows build)" = "77yINjQg44OT44OD44OIIHg2NCDniYjjga4gUHl0aG9uIOOBjOW/heimgeOBp+OBmSAtIFB5VG9yY2gg44Gr44GvIDMyIOODk+ODg+ODiOeJiOOChCBBUk02NCDniYjjga4gV2luZG93cyDlkJHjgZHjg5Pjg6vjg4njgYzjgYLjgorjgb7jgZvjgpPvvIk="
    "Download Python 3.12 from python.org and install it, just for you?" = "cHl0aG9uLm9yZyDjgYvjgokgUHl0aG9uIDMuMTIg44KS44OA44Km44Oz44Ot44O844OJ44GX44Gm44CB44GT44Gu44Om44O844K244O844Gg44GR44Gr44Kk44Oz44K544OI44O844Or44GX44G+44GZ44GL77yf"
    "couldn't download or run the Python installer: *" = "UHl0aG9uIOOBruOCpOODs+OCueODiOODvOODqeODvOOCkuODgOOCpuODs+ODreODvOODieOBvuOBn+OBr+Wun+ihjOOBp+OBjeOBvuOBm+OCk+OBp+OBl+OBnzogKg=="
    "Download Calibre (about 220 MB) from calibre-ebook.com and install it?" = "Y2FsaWJyZS1lYm9vay5jb20g44GL44KJIENhbGlicmXvvIjntIQgMjIwIE1C77yJ44KS44OA44Km44Oz44Ot44O844OJ44GX44Gm44Kk44Oz44K544OI44O844Or44GX44G+44GZ44GL77yf"
    "Windows will ask for permission to install it. If nothing appears, look for a flashing icon on the taskbar." = "44Kk44Oz44K544OI44O844Or44Gu6Kix5Y+v44KSIFdpbmRvd3Mg44GM5rGC44KB44G+44GZ44CC5L2V44KC6KGo56S644GV44KM44Gq44GE5aC05ZCI44Gv44CB44K/44K544Kv44OQ44O844Gn54K55ruF44GX44Gm44GE44KL44Ki44Kk44Kz44Oz44KS5o6i44GX44Gm44GP44Gg44GV44GE44CC"
    "couldn't download or run the Calibre installer: *" = "Q2FsaWJyZSDjga7jgqTjg7Pjgrnjg4jjg7zjg6njg7zjgpLjg4Djgqbjg7Pjg63jg7zjg4njgb7jgZ/jga/lrp/ooYzjgafjgY3jgb7jgZvjgpPjgafjgZfjgZ86ICo="
    "This can take a few minutes, with nothing to show until it's done." = "5pWw5YiG44GL44GL44KL44GT44Go44GM44GC44KK44CB57WC44KP44KL44G+44Gn5L2V44KC6KGo56S644GV44KM44G+44Gb44KT44CC"
}
$Japanese = [ordered]@{}
foreach ($k in $JapaneseB64.Keys) {
    $Japanese[$k] = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($JapaneseB64[$k]))
}

function Tr($msg) {
    $table = switch ($Lang) { "fr" { $French } "es" { $Spanish } "ja" { $Japanese } default { $null } }
    if ($null -eq $table) { return $msg }
    if ($table.Contains($msg)) { return $table[$msg] }
    foreach ($key in $table.Keys) {
        if ($key.EndsWith("*") -and $msg.StartsWith($key.TrimEnd("*"))) {
            return $table[$key].TrimEnd("*") + $msg.Substring($key.Length - 1)
        }
    }
    return $msg
}

# --- output helpers ----------------------------------------------------------
function Write-Step($msg) { Write-Host ""; Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host (Tr $msg) -ForegroundColor White }
function Write-Ok($msg)   { Write-Host "  [ok] " -ForegroundColor Green -NoNewline; Write-Host (Tr $msg) }
function Write-Warn($msg) { Write-Host "  [!] " -ForegroundColor Yellow -NoNewline; Write-Host (Tr $msg) }
function Write-Dim($msg)  { Write-Host "  $(Tr $msg)" -ForegroundColor DarkGray }
# `exit` ends the PowerShell process itself when this script arrives through
# `irm | iex`, so the window would close on the very message it had just
# printed. Run as a file, exit as usual (CI relies on the exit code); piped,
# stop with an error instead and leave the window, and the message, where they
# are.
$RunAsFile = [bool]$PSCommandPath
function Fail($msg) {
    Write-Host ""; Write-Host "error: $(Tr $msg)" -ForegroundColor Red
    if ($RunAsFile) { exit 1 }
    throw "ebook-audiobook: install stopped"
}

# Runs a native command whose stderr is redirected. Windows PowerShell 5.1
# turns each line a redirected native command writes to stderr into an error
# record, and under "Stop" the first one aborts the whole script - so a Python
# warning, or gh's progress output, would end the install with a
# NativeCommandError. The exit code is still in $LASTEXITCODE afterwards.
function Invoke-Native([scriptblock]$Block) {
    $ErrorActionPreference = "Continue"
    & $Block
}

function Ask($question, $default = "y") {
    $question = Tr $question
    $hint = if ($Lang -eq "fr") { if ($default -eq "y") { "[O/n]" } else { "[o/N]" } }
            else { if ($default -eq "y") { "[Y/n]" } else { "[y/N]" } }
    if ($Yes) { Write-Host "  $question $hint $default (auto)"; return ($default -eq "y") }
    $reply = Read-Host "  $question $hint"
    if ([string]::IsNullOrWhiteSpace($reply)) { $reply = $default }
    if ($reply -match '^(o|oui)$') { $reply = "y" }
    return ($reply.Trim().ToLower() -in @("y", "yes"))
}

# --- locations ---------------------------------------------------------------
$DataDir = if ($InstallDir) { $InstallDir } else { Join-Path $env:LOCALAPPDATA "ebook-audiobook" }
$VenvDir = Join-Path $DataDir "venv"
$BinDir  = Join-Path $DataDir "bin"
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"

# A running copy holds its own .exe files open, and Windows will not replace or
# delete a file that is in use: pip dies halfway through an upgrade with
# "Access is denied", and an uninstall leaves half a program behind. Find it
# first. Every process in the chain - the entry-point .exe, the venv's
# python.exe redirector and the real interpreter it starts - carries the venv
# path in its executable path or its command line.
function Stop-RunningApp {
    if (-not (Test-Path $VenvDir)) { return }
    $procs = @()
    try {
        $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $procs = @($all | Where-Object {
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($VenvDir, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.CommandLine -and $_.CommandLine.IndexOf($VenvDir, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        })
        # The app's own "Install update" runs this script as its child. Then
        # the app is not in the way, it is the caller, waiting for the result:
        # leave it be, as it expects.
        $byId = @{}
        foreach ($p in $all) { $byId[[int]$p.ProcessId] = $p }
        $ancestors = @{}
        $cur = [int]$PID
        for ($i = 0; $i -lt 64 -and $byId.ContainsKey($cur); $i++) {
            $ancestors[$cur] = $true
            $parent = [int]$byId[$cur].ParentProcessId
            # Windows reuses process ids: a long-dead parent's id may belong to
            # something newer now. A real parent is older than its child.
            if (-not $byId.ContainsKey($parent) -or
                $byId[$parent].CreationDate -gt $byId[$cur].CreationDate) { break }
            $cur = $parent
        }
        foreach ($p in $procs) { if ($ancestors.ContainsKey([int]$p.ProcessId)) { return } }
    } catch { return }
    if ($procs.Count -eq 0) { return }
    Write-Warn "ebook-audiobook is running, and has to close first"
    if (-not (Ask "Close it now? A render in progress stops, and picks up where it left off when you start it again." "y")) {
        Fail "quit ebook-audiobook from its tray icon, then run this again."
    }
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

# --- uninstall ---------------------------------------------------------------
if ($Uninstall) {
    Write-Step "Uninstalling ebook-audiobook"
    Stop-RunningApp
    if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
    if (Test-Path $BinDir)  { Remove-Item -Recurse -Force $BinDir }
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ebook-audiobook.lnk"
    if (Test-Path $startMenu) { Remove-Item -Force $startMenu }
    $desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "ebook-audiobook.lnk"
    if (Test-Path $desktopLnk) { Remove-Item -Force $desktopLnk }
    # The app window's own browser profile. Regenerated on next launch; holds no
    # books, settings or audiobooks, only Chromium's window-size cache.
    $profileDir = Join-Path $DataDir "browser-profile"
    if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir }

    # Take our entry back out of the user PATH, leaving everything else alone.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -and $userPath.Split(';') -contains $BinDir) {
        $kept = ($userPath.Split(';') | Where-Object { $_ -and $_ -ne $BinDir }) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $kept, "User")
    }
    Write-Ok "program removed"
    Write-Host ""
    Write-Host (Tr "  Your books, settings, and audiobooks were NOT deleted. They're in:")
    Write-Host "    $DataDir"
    Write-Host (Tr "  Delete that folder yourself if you want them gone.")
    # Not `exit`: under `irm | iex` that would close the window. At the top
    # level of the script, `return` ends the script and nothing more.
    return
}

Write-Host ""
Write-Host (Tr "ebook-audiobook installer") -ForegroundColor White
Write-Host (Tr "Turns ebooks you own into narrated audiobooks, entirely offline.") -ForegroundColor DarkGray

# --- 1. Python ---------------------------------------------------------------
Write-Step "Looking for Python 3.11 or newer"

# "3.12 win-amd64" for an interpreter that runs, $null for one that doesn't.
function Get-PythonInfo($exe) {
    try {
        $info = Invoke-Native { & $exe -c "import sys, sysconfig; print('%d.%d %s' % (sys.version_info[0], sys.version_info[1], sysconfig.get_platform()))" 2>$null }
        if ($LASTEXITCODE -eq 0 -and $info) { return ("$info").Trim() }
    } catch {}
    return $null
}

# Most-tested first: releases are verified on 3.12. Anything newer than 3.13 is
# accepted, but only when nothing better is installed.
function Get-PythonRank($ver) {
    switch ($ver) { "3.12" { 0 } "3.13" { 1 } "3.11" { 2 } default { 3 } }
}

function Find-Python {
    $candidates = New-Object System.Collections.Generic.List[string]
    # The py launcher knows every registered install, and -0p lists their paths.
    # Asking for `python` alone can hit the Microsoft Store stub, which is not a
    # real interpreter and silently does nothing useful.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $listed = Invoke-Native { & py -0p 2>$null }
            foreach ($line in @($listed)) {
                if ("$line" -match '([A-Za-z]:\\.*?pythonw?\.exe)(\s+\*)?\s*$') { $candidates.Add($Matches[1]) }
            }
        } catch {}
    }
    foreach ($name in @("python3.12", "python3.13", "python3.11", "python", "python3")) {
        # Every match, not the first: the Store stub is often first on PATH,
        # ahead of a real interpreter.
        foreach ($cmd in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            if ($cmd.Source) { $candidates.Add($cmd.Source) }
        }
    }
    # Where python.org's installer puts things, for when PATH hasn't caught up:
    # a winget install a moment ago, or "Add python.exe to PATH" left unticked.
    foreach ($root in @((Join-Path $env:LOCALAPPDATA "Programs\Python"), $env:ProgramFiles)) {
        if ($root -and (Test-Path $root)) {
            Get-ChildItem -Path $root -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
                ForEach-Object { $candidates.Add((Join-Path $_.FullName "python.exe")) }
        }
    }

    $best = $null; $bestRank = 99; $seen = @{}
    foreach ($c in $candidates) {
        if (-not $c -or $seen.ContainsKey($c.ToLower())) { continue }
        $seen[$c.ToLower()] = $true
        # Anything under WindowsApps is either the Store stub or the Store's
        # own Python, whose writes under AppData can be redirected into its
        # sandbox - the environment created below could land somewhere this
        # script, and the Start Menu shortcut, can't see.
        if ($c -like "*\WindowsApps\*" -or -not (Test-Path $c)) { continue }
        $info = Get-PythonInfo $c
        if (-not $info) { continue }
        $ver, $plat = $info -split ' ', 2
        # 64-bit x86 only. PyTorch publishes no 32-bit Windows build, and has no
        # torchaudio for ARM64 Windows: on an ARM64 PC it is an x64 Python,
        # which Windows runs by emulation, that can render.
        if ($plat -ne "win-amd64") { continue }
        $major, $minor = $ver.Split('.')
        if ([int]$major -ne 3 -or [int]$minor -lt 11) { continue }
        $rank = Get-PythonRank $ver
        if ($rank -lt $bestRank) { $best = $c; $bestRank = $rank }
    }
    return $best
}

function Update-SessionPath {
    # Installers update PATH for new processes only; refresh ours so what was
    # just installed is findable without opening a new window.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

$Python = Find-Python
if (-not $Python) {
    Write-Warn "no Python 3.11+ found"
    Write-Dim "(it must be 64-bit x64 Python - PyTorch has no 32-bit or ARM64 Windows build)"
    $declined = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Ask "Install Python 3.12 now with winget?" "y") {
            winget install --id Python.Python.3.12 --source winget --scope user --architecture x64 `
                --accept-package-agreements --accept-source-agreements --silent
            Update-SessionPath
            $Python = Find-Python
        } else { $declined = $true }
    }
    # winget is missing on some fresh or managed machines, and on others its
    # source is broken until it has been updated. python.org's own installer
    # needs neither, nor administrator rights when it installs just for you.
    if (-not $Python -and -not $declined) {
        $pyUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
        if (Ask "Download Python 3.12 from python.org and install it, just for you?" "y") {
            $pyDl = Join-Path $DataDir ".download"
            $pyInstaller = Join-Path $pyDl "python-3.12.10-amd64.exe"
            try {
                New-Item -ItemType Directory -Force -Path $pyDl | Out-Null
                Write-Dim $pyUrl
                Write-Dim "This can take a few minutes, with nothing to show until it's done."
                Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
                # Silent and per-user. PATH, the py launcher and file
                # associations are left alone; Find-Python looks where this
                # installs, and this app is all it is here for.
                Start-Process -FilePath $pyInstaller -Wait -ArgumentList @(
                    "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=0",
                    "AssociateFiles=0", "Shortcuts=0", "Include_test=0", "Include_doc=0")
            } catch {
                Write-Warn "couldn't download or run the Python installer: $_"
            }
            Remove-Item -Recurse -Force $pyDl -ErrorAction SilentlyContinue
            $Python = Find-Python
        }
    }
}
if (-not $Python) {
    Fail "64-bit Python 3.11+ is required.`n       Install it from https://www.python.org/downloads/`n       (tick 'Add python.exe to PATH'), then re-run this installer."
}
$pyVer = & $Python -c "import platform; print(platform.python_version())"
Write-Ok "Python $pyVer at $Python"

# --- 2. virtualenv -----------------------------------------------------------
Write-Step "Creating a private environment"
Write-Dim $VenvDir
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Stop-RunningApp
# An environment is only worth reusing if it still runs: one whose Python has
# since been uninstalled, or that an interrupted install left half-built, is
# rebuilt instead. Nothing of the user's lives in it.
$venvWorks = $false
# ...and only if it is one this installer would pick today: a venv built on a
# 32-bit or ARM64 Python runs fine and still can never install the engine.
if (Test-Path $VenvPy) { $venvWorks = "$(Get-PythonInfo $VenvPy)" -match '^3\.(1[1-9]|[2-9]\d) win-amd64$' }
if ($venvWorks) {
    Write-Ok "reusing the existing environment (upgrading in place)"
} else {
    & $Python -m venv --clear $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "couldn't create a virtualenv at $VenvDir" }
    Write-Ok "created"
}
if (-not (Test-Path $VenvPy)) { Fail "the environment at $VenvDir looks broken; delete it and re-run" }
& $VenvPy -m pip install --quiet --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Fail "couldn't upgrade pip" }

# --- 3. the app --------------------------------------------------------------
Write-Step "Installing ebook-audiobook"
# $PSScriptRoot is empty when this is piped through `iex`, so a bare
# "is there a pyproject.toml?" test could pick up an unrelated project the user
# happens to be standing in. Require that it is specifically this one.
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
$isSourceTree = $false
if ($scriptDir) {
    $pyproject = Join-Path $scriptDir "pyproject.toml"
    if ((Test-Path $pyproject) -and
        (Test-Path (Join-Path $scriptDir "ebook_audiobook")) -and
        (Select-String -Path $pyproject -Pattern '^name = "ebook-audiobook"' -Quiet)) {
        $isSourceTree = $true
    }
}
if ($isSourceTree) {
    # Running from a checkout: install from source. Used by CI to smoke-test this
    # installer, and by anyone testing a change before cutting a release.
    Write-Dim "installing from the source tree at $scriptDir"
    # setuptools reuses build\lib from an earlier install and never removes a
    # file from it, so anything deleted from the source tree since would still
    # ride along in the wheel. Start from nothing every time.
    Remove-Item -Recurse -Force (Join-Path $scriptDir "build") -ErrorAction SilentlyContinue
    & $VenvPy -m pip install --quiet $scriptDir
} else {
    $resolved = $Version
    if ($resolved -eq "latest") {
        if ($PinnedVersion -notlike "__EBAB_*") {
            $resolved = $PinnedVersion
        } else {
            # Unreleased copy of this script: ask GitHub what's current.
            try {
                $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
                                         -Headers @{ "User-Agent" = "ebook-audiobook-installer" }
                $resolved = $rel.tag_name -replace '^v', ''
            } catch {
                Fail "couldn't work out which version to install.`n       Pass one explicitly, e.g. -Version 1.0.0`n       Releases: https://github.com/$Repo/releases"
            }
        }
    }
    $wheelUrl = "https://github.com/$Repo/releases/download/v$resolved/ebook_audiobook-$resolved-py3-none-any.whl"
    Write-Dim $wheelUrl

    # Download first, install second, so an HTTP problem produces a clear message
    # rather than pip's wall of 404 text. Release assets of a *private* repo
    # aren't publicly readable, so fall back to an authenticated fetch via the gh
    # CLI when one is available; that makes the same one-liner work for the repo
    # owner before the project is made public.
    #
    # The download must keep the wheel's real filename: pip parses the package
    # name and version out of it, so a renamed or dot-prefixed file is rejected.
    $wheelName = "ebook_audiobook-$resolved-py3-none-any.whl"
    $dlDir = Join-Path $DataDir ".download"
    New-Item -ItemType Directory -Force -Path $dlDir | Out-Null
    $localWheel = Join-Path $dlDir $wheelName

    $got = $false
    try {
        Invoke-WebRequest -Uri $wheelUrl -OutFile $localWheel -UseBasicParsing
        $got = Test-Path $localWheel
    } catch { $got = $false }

    if (-not $got -and (Get-Command gh -ErrorAction SilentlyContinue)) {
        Invoke-Native { & gh release download "v$resolved" -R $Repo -p $wheelName -O $localWheel --clobber 2>$null }
        if ($LASTEXITCODE -eq 0 -and (Test-Path $localWheel)) {
            Write-Dim "(downloaded with your GitHub credentials - the repo isn't public yet)"
            $got = $true
        }
    }

    if (-not $got) {
        Remove-Item -Recurse -Force $dlDir -ErrorAction SilentlyContinue
        Fail "couldn't download $wheelName.`n       If the release exists, this usually means the repository is still private.`n       See https://github.com/$Repo/releases"
    }

    & $VenvPy -m pip install --quiet --upgrade $localWheel
    Remove-Item -Recurse -Force $dlDir -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -ne 0) { Fail "couldn't install the app. Check your connection, or see https://github.com/$Repo/releases" }
$appVer = & $VenvPy -c "import importlib.metadata as m; print(m.version('ebook-audiobook'))"
Write-Ok "installed $appVer"

# --- 4. PyTorch --------------------------------------------------------------
if ($NoTts) {
    Write-Step "Skipping the speech engine (-NoTts)"
    Write-Warn "you can import books, but rendering audio needs the engine"
    Write-Dim "add it later with: `"$VenvDir\Scripts\pip.exe`" install torch torchaudio chatterbox-tts `"setuptools<81`""
} else {
    Write-Step "Setting up the speech engine"
    $hasNvidia = $false
    $gpuName = ""
    $computeCaps = ""
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try {
            # Captured whole, then trimmed to the first line: through a pipeline
            # into Select-Object, $LASTEXITCODE would be the previous command's.
            $smiOut = @(Invoke-Native { & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null })
            if ($LASTEXITCODE -eq 0 -and $smiOut.Count -gt 0 -and "$($smiOut[0])".Trim()) {
                $gpuName = "$($smiOut[0])".Trim()
                $hasNvidia = $true
            }
        } catch {}
        try {
            # One line per GPU. Decides which CUDA build has kernels for the
            # card. Filtered to well-formed values because a broken NVML prints
            # its error to stdout, which would otherwise land here as garbage.
            $caps = Invoke-Native { & nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null } |
                    ForEach-Object { $_.Trim() } |
                    Where-Object { $_ -match '^\d+\.\d+$' }
            if ($caps) { $computeCaps = ($caps -join ",") }
        } catch {}
    }

    # Which PyTorch build belongs here is decided by the app, not by this
    # script, so install.sh and install.ps1 cannot drift apart. The app was
    # installed in section 3, so this module is importable by now.
    $forced = ""
    if ($Cpu) { $forced = "cpu" }
    elseif ($Cuda126) { $forced = "cuda126" }
    elseif ($Cuda128) { $forced = "cuda128" }
    elseif ($Gpu) { $forced = "gpu" }
    $vendor = ""
    if ($hasNvidia) { $vendor = "nvidia" }

    $id = ""; $index = ""; $size = ""; $label = ""; $pin = ""
    # Built as an array and splatted, with empty values omitted entirely.
    # Windows PowerShell 5.1 can silently drop an empty-string argument, which
    # would leave the next flag consuming the wrong value - so never pass one.
    $tbArgs = @("-m", "ebook_audiobook.torchbuild", "--platform", "windows", "--arch", "amd64")
    if ($vendor)  { $tbArgs += @("--vendor", $vendor) }
    if ($forced)  { $tbArgs += @("--forced", $forced) }
    if ($gpuName) { $tbArgs += @("--gpu-name", "$gpuName".Trim()) }
    if ($computeCaps) { $tbArgs += @("--compute-caps", $computeCaps) }
    try {
        $out = Invoke-Native { & $VenvPy @tbArgs 2>$null }
        foreach ($line in $out) {
            $k, $v = $line -split "=", 2
            switch ($k) {
                "EBAB_TORCH_ID"    { $id = $v }
                "EBAB_TORCH_INDEX" { $index = $v }
                "EBAB_TORCH_SIZE"  { $size = $v }
                "EBAB_TORCH_LABEL" { $label = $v }
                "EBAB_TORCH_PIN"   { $pin = $v }
            }
        }
    } catch {}

    if (-not $id) {
        # Only reachable if the app can't be imported (e.g. -Version pinned to a
        # release predating this module). CPU always works; say so rather than
        # guessing at hardware.
        Write-Warn "couldn't ask the app which PyTorch build to use; falling back to CPU-only"
        $id = "cpu"; $index = "https://download.pytorch.org/whl/cpu"; $size = "about 250 MB"; $pin = "2.9.1"
    }

    # The module supplies the facts; this supplies the phrasing.
    if ($id -eq "cu128" -or $id -eq "cu126") {
        if ($forced -eq "gpu") { $desc = "CUDA (forced with -Gpu) - a novel takes roughly 2-3 hours" }
        elseif ($gpuName) { $desc = "$("$gpuName".Trim()) via $label - a novel takes roughly 2-3 hours" }
        else { $desc = "CUDA via $label - a novel takes roughly 2-3 hours" }
    } elseif ($forced -eq "cpu") {
        $desc = "CPU only (forced with -Cpu)"
    } else {
        $desc = "no NVIDIA GPU detected, CPU only - a novel can take many hours"
    }

    Write-Host "  Detected: " -NoNewline; Write-Host $desc -ForegroundColor White
    Write-Host "  Download: " -NoNewline; Write-Host $size -ForegroundColor White

    # An AMD card on Windows is worth naming rather than leaving as a silent
    # "no GPU". PyTorch's ROCm wheels are Linux-only, and the Windows
    # alternative (DirectML) is a different backend that doesn't run this model,
    # so the CPU build genuinely is the right answer here - but a user who just
    # watched us skip past their Radeon deserves to be told why.
    if (-not $hasNvidia -and -not $Cpu -and -not $Gpu) {
        try {
            $amd = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
                   Where-Object { $_.Name -match 'Radeon|AMD' } | Select-Object -First 1
            if ($amd) {
                Write-Dim "$($amd.Name.Trim()) found, but PyTorch's AMD (ROCm) builds are"
                Write-Dim "Linux-only, so this uses the CPU. On Linux the installer picks ROCm."
            }
        } catch {}
    }
    if (Ask "Download and install the speech engine now?" "y") {
        # Three pip commands, in this order, and the order is load-bearing.
        #
        # 1. torch, pinned exactly, from the chosen index. The pin must be
        #    exact: PyPI's torch is far ahead of the pinned indexes and PEP 440
        #    ranks a plain 2.13.0 above 2.9.1+cu128, so a floor would quietly
        #    fetch the default CUDA build from PyPI and undo the choice.
        # 2. Chatterbox with --no-deps. It declares torch==2.6.0, which has no
        #    kernels for current GPUs; letting it resolve drags the pinned
        #    build back down.
        # 3. Chatterbox's dependencies, curated by us (see torchbuild.py), with
        #    the torch pins repeated so nothing there can replace the build.
        $cbPin  = Invoke-Native { & $VenvPy -c "from ebook_audiobook.torchbuild import CHATTERBOX_PIN; print(CHATTERBOX_PIN)" 2>$null }
        $cbDeps = Invoke-Native { & $VenvPy -c "from ebook_audiobook.torchbuild import CHATTERBOX_DEPS; print(' '.join(CHATTERBOX_DEPS))" 2>$null }
        if (-not $cbPin) { $cbPin = "chatterbox-tts" }
        $idxArgs = @()
        if ($index) { $idxArgs = @("--index-url", $index, "--extra-index-url", "https://pypi.org/simple") }
        $pins = @("torch==$pin", "torchaudio==$pin")
        # Gigabytes, often over shared Wi-Fi: pip's defaults give up on a
        # 15-second stall. A download that completed is kept in pip's cache, so
        # re-running after a failure doesn't fetch it again.
        $netArgs = @("--timeout", "60", "--retries", "10")

        & $VenvPy -m pip install --quiet @netArgs @idxArgs @pins
        if ($LASTEXITCODE -eq 0) {
            & $VenvPy -m pip install --quiet @netArgs --no-deps $cbPin
        }
        if ($LASTEXITCODE -eq 0) {
            $depList = @("$cbDeps" -split ' ' | Where-Object { $_ })
            & $VenvPy -m pip install --quiet @netArgs @idxArgs @pins @depList
        }
        if ($LASTEXITCODE -ne 0) {
            Fail ("the speech engine failed to install. Re-run with -Cpu, or by hand:`n" +
                  "         `"$VenvDir\Scripts\pip.exe`" install torch==$pin torchaudio==$pin")
        }
        # Report the build that actually landed. The whole bug above was
        # invisible precisely because nothing said which torch you ended up with.
        $torchBuild = Invoke-Native { & $VenvPy -c "import torch; print(torch.__version__)" 2>$null }
        if ($torchBuild) { Write-Ok "speech engine ready (torch $("$torchBuild".Trim()))" }
        else { Write-Ok "speech engine ready" }
        Write-Dim "The ~3 GB voice model downloads the first time you render."
    } else {
        Write-Warn "skipped - re-run this installer to add it later."
    }
}

# --- 5. Calibre --------------------------------------------------------------
Write-Step "Checking for Calibre (needed to read ebook files)"
# Asks the app, so this finds Calibre wherever the app itself will look.
function Test-Calibre {
    Invoke-Native { & $VenvPy -c "from ebook_audiobook import tools; raise SystemExit(0 if tools.ebook_convert_path() else 1)" 2>$null } | Out-Null
    return ($LASTEXITCODE -eq 0)
}
if (Test-Calibre) {
    Write-Ok "Calibre found"
} else {
    Write-Warn "Calibre is not installed"
    $declined = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Ask "Install it now with 'winget install calibre.calibre'?" "y") {
            winget install --id calibre.calibre --source winget `
                --accept-package-agreements --accept-source-agreements --silent
            # winget's exit code is no verdict: it is non-zero for "already
            # installed" and for "installed, restart recommended". Look instead.
            if (-not (Test-Calibre)) { Write-Warn "winget install failed" }
        } else { $declined = $true }
    }
    # Calibre's own installer, for when winget is missing or broken. It installs
    # for every user, so Windows asks for permission once.
    if (-not (Test-Calibre) -and -not $declined) {
        if (Ask "Download Calibre (about 220 MB) from calibre-ebook.com and install it?" "y") {
            $calDl = Join-Path $DataDir ".download"
            $calMsi = Join-Path $calDl "calibre-64bit.msi"
            try {
                New-Item -ItemType Directory -Force -Path $calDl | Out-Null
                Write-Dim "https://calibre-ebook.com/dist/win64"
                Write-Dim "This can take a few minutes, with nothing to show until it's done."
                Invoke-WebRequest -Uri "https://calibre-ebook.com/dist/win64" -OutFile $calMsi -UseBasicParsing
                Write-Dim "Windows will ask for permission to install it. If nothing appears, look for a flashing icon on the taskbar."
                Start-Process -FilePath "msiexec.exe" -Verb RunAs -Wait -ArgumentList @("/i", "`"$calMsi`"", "/qb", "/norestart")
            } catch {
                Write-Warn "couldn't download or run the Calibre installer: $_"
            }
            Remove-Item -Recurse -Force $calDl -ErrorAction SilentlyContinue
        }
    }
    if (Test-Calibre) {
        Write-Ok "Calibre installed"
    } else {
        Write-Warn "install Calibre before converting a book:"
        Write-Host "      winget install --id calibre.calibre"
        Write-Dim "or download it from https://calibre-ebook.com/download"
    }
}

# --- 6. launchers ------------------------------------------------------------
Write-Step "Creating the launcher"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# A .cmd shim rather than a copied .exe, so upgrading the venv can never leave a
# stale launcher pointing at a deleted interpreter. It finds the venv relative
# to itself (%~dp0 is the shim's own folder) rather than by absolute path:
# cmd.exe reads a batch file in the console's code page, so a path with a
# character outside ASCII - C:\Users\Zoë\... - would arrive mangled.
$shim = Join-Path $BinDir "ebook-audiobook.cmd"
@"
@echo off
REM Generated by the ebook-audiobook installer.
"%~dp0..\venv\Scripts\ebook-audiobook.exe" %*
"@ | Set-Content -Path $shim -Encoding ASCII
Write-Ok "command: ebook-audiobook"

# Put the shim on the *user* PATH (never the machine PATH: no admin, no
# surprises for other accounts).
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath.Split(';') -notcontains $BinDir) {
    $newPath = if ($userPath.TrimEnd(';')) { "$($userPath.TrimEnd(';'));$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$env:Path;$BinDir"
    Write-Ok "added to your PATH (new terminals will find it)"
}

# Start Menu shortcut. Points at the gui_scripts entry point, which Windows runs
# with pythonw.exe, so opening it doesn't leave a console window behind the
# browser.
$guiExe = Join-Path $VenvDir "Scripts\ebook-audiobook-gui.exe"
$targetExe = if (Test-Path $guiExe) { $guiExe } else { Join-Path $VenvDir "Scripts\ebook-audiobook.exe" }

# The app's own icon, shipped inside the wheel. Without this the shortcut shows
# the generic console-application icon that setuptools stamps into every
# entry-point .exe, which is the same icon as every other Python tool installed.
$iconPath = ""
try {
    $assets = & $VenvPy -c "import ebook_audiobook, pathlib; print(pathlib.Path(ebook_audiobook.__file__).parent / 'assets')"
    $candidate = Join-Path $assets "icon.ico"
    if (Test-Path $candidate) { $iconPath = $candidate }
} catch {
    # Non-fatal: a shortcut with the default icon still launches the app.
}

try {
    $shell = New-Object -ComObject WScript.Shell
    $startMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    New-Item -ItemType Directory -Force -Path $startMenuDir | Out-Null
    $lnk = $shell.CreateShortcut((Join-Path $startMenuDir "ebook-audiobook.lnk"))
    $lnk.TargetPath = $targetExe
    $lnk.WorkingDirectory = $DataDir
    $lnk.Description = "Turn ebooks you own into narrated audiobooks"
    if ($iconPath) { $lnk.IconLocation = "$iconPath,0" }
    $lnk.Save()
    Write-Ok "Start Menu shortcut"

    if (Ask "Add a Desktop shortcut too?" "y") {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $dlnk = $shell.CreateShortcut((Join-Path $desktop "ebook-audiobook.lnk"))
        $dlnk.TargetPath = $targetExe
        $dlnk.WorkingDirectory = $DataDir
        $dlnk.Description = "Turn ebooks you own into narrated audiobooks"
        if ($iconPath) { $dlnk.IconLocation = "$iconPath,0" }
        $dlnk.Save()
        Write-Ok "Desktop shortcut"
    }
} catch {
    Write-Warn "couldn't create shortcuts: $_"
}

# --- done --------------------------------------------------------------------
Write-Step "Verifying the install"
Invoke-Native { & $VenvPy -m ebook_audiobook.cli check --engine fake > $null 2>&1 }
if ($LASTEXITCODE -eq 0) {
    Write-Ok "all required components are working"
} else {
    Write-Warn "some checks failed - run 'ebook-audiobook check' for details"
}

Write-Host ""
Write-Host (Tr "Installed.") -ForegroundColor Green
Write-Host ""
Write-Host (Tr "  Start it from the Start Menu, or run: ") -NoNewline; Write-Host "ebook-audiobook" -ForegroundColor White
Write-Host (Tr "  Your books live in: ") -NoNewline; Write-Host $DataDir -ForegroundColor DarkGray
Write-Host (Tr "  Check setup:  ") -NoNewline; Write-Host "ebook-audiobook check" -ForegroundColor DarkGray
Write-Host (Tr "  Uninstall:    ") -NoNewline; Write-Host "iex `"& { `$(irm https://github.com/$Repo/releases/latest/download/install-windows.ps1) } -Uninstall`"" -ForegroundColor DarkGray
Write-Host ""
Write-Host (Tr "  Note: open a NEW terminal for the 'ebook-audiobook' command to be found.") -ForegroundColor DarkGray
Write-Host ""
