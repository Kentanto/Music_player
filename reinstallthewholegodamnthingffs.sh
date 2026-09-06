#!/bin/bash

cd ~/Music/Music_player || exit 1

echo "Checking for latest Music Engine files..."

# Make sure we're on the main branch
git checkout main || exit 1

# Pull latest changes from GitHub
git pull --ff-only || {
    echo "ERROR: Could not update from GitHub."
    echo "There may be local changes or a divergent branch."
    exit 1
}

echo "Latest files pulled successfully."

# Activate virtual environment
source .venv/bin/activate || exit 1

echo "Cleaning previous build..."

# Clean previous builds
rm -rf build dist

echo "Building Music Engine..."

# Build with PyInstaller
python3 -m PyInstaller --clean MusicEngine.spec || exit 1

echo "Installing Music Engine..."

# Remove previous installation
rm -rf ~/.local/opt/MusicEngine

# Create installation directory
mkdir -p ~/.local/opt/MusicEngine

# Install new build
cp -r dist/MusicEngine/* ~/.local/opt/MusicEngine/

# Make executable
chmod +x ~/.local/opt/MusicEngine/MusicEngine

# Update desktop application database
update-desktop-database ~/.local/share/applications

echo "Music Engine rebuilt and installed successfully."
