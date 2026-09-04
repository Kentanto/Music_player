#!/bin/bash

cd ~/Music/Music_player || exit 1

echo "Pulling latest Music Engine files from GitHub..."

# Make sure we're on the main branch
git checkout main || exit 1

# Pull the newest changes from GitHub
git pull --ff-only origin main || {
    echo "ERROR: Could not update from GitHub."
    echo "Your local repository may have changes that need attention."
    exit 1
}

echo "Latest files pulled successfully."

# Activate virtual environment
source .venv/bin/activate || exit 1

# Clean previous builds
rm -rf build dist

# Build with PyInstaller
python3 -m PyInstaller --clean MusicEngine.spec || exit 1

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
