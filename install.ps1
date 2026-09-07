<#
.SYNOPSIS
  ebook-audiobook installer for Windows.

.DESCRIPTION
  Run this in PowerShell:

    irm https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install.ps1 | iex

  What it does, in order:
    1. finds a Python 3.11+ interpreter (offers to install one via winget)
    2. creates a private virtualenv under %LOCALAPPDATA%\ebook-audiobook
    3. installs the app, plus the right PyTorch build for this machine
    4. checks for Calibre and offers to install it
    5. adds an `ebook-audiobook` command and a Start Menu shortcut

  Everything is per-user. No administrator rights are needed, nothing is written
  outside your own profile, and your books and settings are never touched by an
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
    "The ~1 GB voice model downloads the first time you render." = "Le modèle vocal (~3 Go) se télécharge à la première narration."
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
    "The ~1 GB voice model downloads the first time you render." = "El modelo de voz (~1 GB) se descarga la primera vez que generes audio."
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
    "The ~1 GB voice model downloads the first time you render." = "6Z+z5aOw44Oi44OH44Or77yI57SEIDEgR0LvvInjga/jgIHmnIDliJ3jgavnlJ/miJDjgZnjgovjgajjgY3jgavjg4Djgqbjg7Pjg63jg7zjg4njgZXjgozjgb7jgZnjgII="
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
function Fail($msg) { Write-Host ""; Write-Host "error: $(Tr $msg)" -ForegroundColor Red; exit 1 }

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

# --- uninstall ---------------------------------------------------------------
if ($Uninstall) {
    Write-Step "Uninstalling ebook-audiobook"
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
    exit 0
}

Write-Host ""
Write-Host (Tr "ebook-audiobook installer") -ForegroundColor White
Write-Host (Tr "Turns ebooks you own into narrated audiobooks, entirely offline.") -ForegroundColor DarkGray

# --- 1. Python ---------------------------------------------------------------
Write-Step "Looking for Python 3.11 or newer"

function Test-PythonVersion($exe) {
    try {
        # 3.11+ or nothing: older versions can't run the app.
        & $exe -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    # The py launcher is the reliable way to pick a version on Windows; asking
    # for `python` alone can hit the Microsoft Store stub, which is not a real
    # interpreter and silently does nothing useful.
    foreach ($v in @("3.13", "3.12", "3.11")) {
        try {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p)) { return $p }
        } catch {}
    }
    foreach ($name in @("python3.13", "python3.12", "python3.11", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            # Store stubs live under WindowsApps and are 0 bytes of nothing.
            if ($cmd.Source -like "*WindowsApps*") { continue }
            if (Test-PythonVersion $cmd.Source) { return $cmd.Source }
        }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    Write-Warn "no Python 3.11+ found"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Ask "Install Python 3.12 now with winget?" "y") {
            winget install --id Python.Python.3.12 --source winget `
                --accept-package-agreements --accept-source-agreements --silent
            # winget updates PATH for new processes only; refresh ours so the
            # freshly installed interpreter is findable without a restart.
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [Environment]::GetEnvironmentVariable("Path", "User")
            $Python = Find-Python
        }
    }
}
if (-not $Python) {
    Fail "Python 3.11+ is required.`n       Install it from https://www.python.org/downloads/`n       (tick 'Add python.exe to PATH'), then re-run this installer."
}
$pyVer = & $Python -c "import platform; print(platform.python_version())"
Write-Ok "Python $pyVer at $Python"

# --- 2. virtualenv -----------------------------------------------------------
Write-Step "Creating a private environment"
Write-Dim $VenvDir
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
if (Test-Path $VenvPy) {
    Write-Ok "reusing the existing environment (upgrading in place)"
} else {
    & $Python -m venv $VenvDir
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
        & gh release download "v$resolved" -R $Repo -p $wheelName -O $localWheel --clobber 2>$null
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
            $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $gpuName) { $hasNvidia = $true }
        } catch {}
        try {
            # One line per GPU. Decides which CUDA build has kernels for the
            # card. Filtered to well-formed values because a broken NVML prints
            # its error to stdout, which would otherwise land here as garbage.
            $caps = & nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null |
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
    if ($gpuName) { $tbArgs += @("--gpu-name", $gpuName.Trim()) }
    if ($computeCaps) { $tbArgs += @("--compute-caps", $computeCaps) }
    try {
        $out = & $VenvPy @tbArgs 2>$null
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
        else { $desc = "$($gpuName.Trim()) via $label - a novel takes roughly 2-3 hours" }
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
        $cbPin  = (& $VenvPy -c "from ebook_audiobook.torchbuild import CHATTERBOX_PIN; print(CHATTERBOX_PIN)" 2>$null)
        $cbDeps = (& $VenvPy -c "from ebook_audiobook.torchbuild import CHATTERBOX_DEPS; print(' '.join(CHATTERBOX_DEPS))" 2>$null)
        if (-not $cbPin) { $cbPin = "chatterbox-tts" }
        $idxArgs = @()
        if ($index) { $idxArgs = @("--index-url", $index, "--extra-index-url", "https://pypi.org/simple") }
        $pins = @("torch==$pin", "torchaudio==$pin")

        & $VenvPy -m pip install --quiet @idxArgs @pins
        if ($LASTEXITCODE -eq 0) {
            & $VenvPy -m pip install --quiet --no-deps $cbPin
        }
        if ($LASTEXITCODE -eq 0) {
            $depList = @($cbDeps -split ' ' | Where-Object { $_ })
            & $VenvPy -m pip install --quiet @idxArgs @pins @depList
        }
        if ($LASTEXITCODE -ne 0) {
            Fail ("the speech engine failed to install. Re-run with -Cpu, or by hand:`n" +
                  "         `"$VenvDir\Scripts\pip.exe`" install torch==$pin torchaudio==$pin")
        }
        # Report the build that actually landed. The whole bug above was
        # invisible precisely because nothing said which torch you ended up with.
        $torchBuild = (& $VenvPy -c "import torch; print(torch.__version__)" 2>$null)
        if ($torchBuild) { Write-Ok "speech engine ready (torch $($torchBuild.Trim()))" }
        else { Write-Ok "speech engine ready" }
        Write-Dim "The ~1 GB voice model downloads the first time you render."
    } else {
        Write-Warn "skipped - re-run this installer to add it later."
    }
}

# --- 5. Calibre --------------------------------------------------------------
Write-Step "Checking for Calibre (needed to read ebook files)"
& $VenvPy -c "from ebook_audiobook import tools; raise SystemExit(0 if tools.ebook_convert_path() else 1)" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Ok "Calibre found"
} else {
    Write-Warn "Calibre is not installed"
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Ask "Install it now with 'winget install calibre.calibre'?" "y") {
            winget install --id calibre.calibre --source winget `
                --accept-package-agreements --accept-source-agreements --silent
            if ($LASTEXITCODE -eq 0) { $installed = $true } else { Write-Warn "winget install failed" }
        }
    }
    if ($installed) {
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
# stale launcher pointing at a deleted interpreter.
$shim = Join-Path $BinDir "ebook-audiobook.cmd"
@"
@echo off
REM Generated by the ebook-audiobook installer.
"$VenvDir\Scripts\ebook-audiobook.exe" %*
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
    $assets = & (Join-Path $VenvDir "Scripts\python.exe") -c `
        "import ebook_audiobook, pathlib; print(pathlib.Path(ebook_audiobook.__file__).parent / 'assets')"
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
& $VenvPy -m ebook_audiobook.cli check --engine fake > $null 2>&1
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
Write-Host (Tr "  Uninstall:    ") -NoNewline; Write-Host "iex `"& { `$(irm https://github.com/$Repo/releases/latest/download/install.ps1) } -Uninstall`"" -ForegroundColor DarkGray
Write-Host ""
Write-Host (Tr "  Note: open a NEW terminal for the 'ebook-audiobook' command to be found.") -ForegroundColor DarkGray
Write-Host ""
