#!/bin/bash
#
# Rebuild and install Music Engine.
#
# The same script covers two situations:
#
#   1. An existing checkout (e.g. the Raspberry Pi's ~/Music/Music_player):
#      behaves exactly like before — update from GitHub, build, install.
#   2. A fresh Linux machine: installs any missing system packages
#      (git / python3 / venv / ffmpeg / Qt libraries), clones the
#      repository if needed, creates the virtual environment with every
#      Python dependency (PyInstaller included), then builds, installs,
#      and adds the application-menu entry.
#
# Optional overrides:
#   REPO_URL=<url> BRANCH=<name> bash reinstallthewholegodamnthingffs.sh
#

REPO_URL="${REPO_URL:-https://github.com/Kentanto/Music_player.git}"
BRANCH="${BRANCH:-main}"
DEFAULT_DIR="$HOME/Music/Music_player"
INSTALL_DIR="$HOME/.local/opt/MusicEngine"
DESKTOP_DIR="$HOME/.local/share/applications"

# Python dependencies, pinned to the versions the working setups use.
# (spotipy pulls in requests/urllib3 by itself.)
PY_PACKAGES="PySide6==6.11.2 yt-dlp==2026.8.19 spotipy==2.26.0 dbus-fast==5.2.0 pyinstaller"

msg()  { echo "[music-engine] $*"; }
fail() { echo "[music-engine] ERROR: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------- system packages

PKG_MANAGER=""
detect_pkg_manager() {
    if have apt-get; then PKG_MANAGER=apt
    elif have dnf; then PKG_MANAGER=dnf
    elif have pacman; then PKG_MANAGER=pacman
    elif have zypper; then PKG_MANAGER=zypper
    fi
}

install_system_packages() {
    detect_pkg_manager
    local required libs
    case "$PKG_MANAGER" in
        apt)
            required="git python3 python3-venv python3-pip ffmpeg"
            libs="libgl1 libegl1 libxkbcommon0 libfontconfig1 libglib2.0-0 libdbus-1-3 libxcb-cursor0 desktop-file-utils"
            ;;
        dnf)
            required="git python3 python3-pip ffmpeg"
            libs="mesa-libGL mesa-libEGL libxkbcommon fontconfig glib2 dbus-libs desktop-file-utils"
            ;;
        pacman)
            required="git python python-pip ffmpeg"
            libs="libglvnd libxkbcommon fontconfig glib2 desktop-file-utils"
            ;;
        zypper)
            required="git python3 python3-pip ffmpeg"
            libs="libglvnd libxkbcommon fontconfig glib2 desktop-file-utils"
            ;;
        *)
            fail "no supported package manager found (apt/dnf/pacman/zypper). Install git, python3 (with venv), pip and ffmpeg manually, then re-run."
            ;;
    esac

    local sudo_cmd=""
    if [ "$(id -u)" -ne 0 ]; then
        have sudo || fail "root rights are needed to install: $required $libs. Install them manually and re-run."
        sudo_cmd="sudo"
    fi

    if have rpm-ostree; then
        msg "Note: this looks like an atomic distro (Bazzite/Silverblue style) — newly layered packages apply after a reboot."
    fi

    msg "Installing required system packages: $required"
    case "$PKG_MANAGER" in
        apt)    $sudo_cmd apt-get update && $sudo_cmd apt-get install -y $required ;;
        dnf)    $sudo_cmd dnf install -y $required ;;
        pacman) $sudo_cmd pacman -Sy --needed --noconfirm $required ;;
        zypper) $sudo_cmd zypper --non-interactive install $required ;;
    esac
    if [ $? -ne 0 ]; then
        if [ "$PKG_MANAGER" = dnf ]; then
            fail "package installation failed. On plain Fedora, ffmpeg needs RPM Fusion enabled first: https://rpmfusion.org"
        fi
        fail "package installation failed. Install $required manually and re-run."
    fi

    # Qt runtime libraries a fresh/minimal system may lack. Best effort:
    # names occasionally differ per release, so a failure here is only a
    # warning (desktop installs normally have them already).
    msg "Installing Qt runtime libraries (best effort): $libs"
    case "$PKG_MANAGER" in
        apt)    $sudo_cmd apt-get install -y $libs || msg "warning: some libraries in '$libs' were skipped" ;;
        dnf)    $sudo_cmd dnf install -y $libs || msg "warning: some libraries in '$libs' were skipped" ;;
        pacman) $sudo_cmd pacman -Sy --needed --noconfirm $libs || msg "warning: some libraries in '$libs' were skipped" ;;
        zypper) $sudo_cmd zypper --non-interactive install $libs || msg "warning: some libraries in '$libs' were skipped" ;;
    esac
}

# Silent on machines that are already set up (e.g. the Pi); only kicks in
# on a fresh system.
missing_tools=""
have git      || missing_tools="$missing_tools git"
have python3  || missing_tools="$missing_tools python3"
have ffmpeg   || missing_tools="$missing_tools ffmpeg"
if have python3 && ! python3 -c "import venv" >/dev/null 2>&1; then
    missing_tools="$missing_tools python3-venv"
fi
if [ -n "$missing_tools" ]; then
    install_system_packages
    have git && have python3 && have ffmpeg || fail "system packages did not install correctly. Install them manually and re-run."
fi

# Safety net for systems that have the tools but no OpenGL libraries
# (PySide6 needs them at runtime).
if ! ldconfig -p 2>/dev/null | grep -q "libGL\.so"; then
    msg "Warning: libGL was not found — if the app fails to start, install OpenGL libraries."
fi

# --------------------------------------------------------------- locate / clone

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
CAN_PULL=yes

if [ -d "$DEFAULT_DIR/.git" ]; then
    PROJECT_DIR="$DEFAULT_DIR"              # the usual setup (e.g. the Pi)
elif [ -d "$script_dir/.git" ]; then
    PROJECT_DIR="$script_dir"               # run from inside a checkout
elif [ -f "$DEFAULT_DIR/main.py" ]; then
    PROJECT_DIR="$DEFAULT_DIR"; CAN_PULL=no # copied without git
elif [ -f "$script_dir/main.py" ]; then
    PROJECT_DIR="$script_dir"; CAN_PULL=no
else
    msg "No existing checkout found — cloning $REPO_URL to $DEFAULT_DIR"
    mkdir -p "$(dirname "$DEFAULT_DIR")"
    git clone "$REPO_URL" "$DEFAULT_DIR" || fail "could not clone the repository"
    PROJECT_DIR="$DEFAULT_DIR"
fi

cd "$PROJECT_DIR" || fail "cannot enter $PROJECT_DIR"

# --------------------------------------------------------------- update from git

if [ "$CAN_PULL" = yes ]; then
    echo "Checking for latest Music Engine files..."

    # Make sure we're on the main branch
    git checkout "$BRANCH" || fail "could not switch to the '$BRANCH' branch"

    # Pull latest changes from GitHub
    git pull --ff-only || {
        echo "ERROR: Could not update from GitHub."
        echo "There may be local changes or a divergent branch."
        exit 1
    }

    echo "Latest files pulled successfully."
else
    msg "Not a git checkout — skipping the update step."
fi

# --------------------------------------------------------------- python environment

if [ ! -d .venv ]; then
    msg "Creating virtual environment..."
    python3 -m venv .venv || fail "could not create .venv (Debian/Ubuntu/Mint: sudo apt install python3-venv)"
fi

# Activate virtual environment
source .venv/bin/activate || fail "could not activate the virtual environment"

msg "Ensuring Python dependencies..."
python3 -m pip install --quiet --upgrade pip || msg "warning: could not upgrade pip (continuing)"

# Only install what is missing, so existing setups keep their versions.
for pkg in $PY_PACKAGES; do
    name="${pkg%%=*}"    # strip ==version for the installed-check
    python3 -m pip show "$name" >/dev/null 2>&1 && continue
    msg "Installing $pkg..."
    python3 -m pip install "$pkg" || fail "could not install $pkg"
done

echo "Cleaning previous build..."

# Clean previous builds
rm -rf build dist

echo "Building Music Engine..."

# Build with PyInstaller
python3 -m PyInstaller --clean MusicEngine.spec || fail "PyInstaller build failed"

echo "Installing Music Engine..."

# Remove previous installation
rm -rf "$INSTALL_DIR"

# Create installation directory
mkdir -p "$INSTALL_DIR"

# Install new build
cp -r dist/MusicEngine/* "$INSTALL_DIR/" || fail "could not copy the build into $INSTALL_DIR"

# Make executable
chmod +x "$INSTALL_DIR/MusicEngine"

# --------------------------------------------------------------- menu entry

mkdir -p "$DESKTOP_DIR"
DESKTOP_FILE="$DESKTOP_DIR/MusicEngine.desktop"
if [ ! -f "$DESKTOP_FILE" ]; then
    msg "Creating the application menu entry..."
    cat > "$DESKTOP_FILE" <<DESKTOP_ENTRY
[Desktop Entry]
Type=Application
Name=Music Engine
Comment=Playlists, YouTube and Spotify-synced music player
Exec=$INSTALL_DIR/MusicEngine
Icon=audio-x-generic
Terminal=false
Categories=AudioVideo;Audio;Music;
DESKTOP_ENTRY
    chmod +x "$DESKTOP_FILE"
fi

# Update desktop application database
have update-desktop-database && update-desktop-database "$DESKTOP_DIR"

# --------------------------------------------------------------- done

echo "Music Engine rebuilt and installed successfully."
echo ""
echo "  Start it from the application menu, or run:"
echo "    $INSTALL_DIR/MusicEngine"
echo ""
echo "  Library tip: to move a library between machines, copy music.db and the"
echo "  playlists folder into the installed app's data folder"
echo "  (~/.local/share/music_engine) — paths are stored portably."
