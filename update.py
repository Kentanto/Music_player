import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def run(command):
    print(f"> {' '.join(str(x) for x in command)}")

    result = subprocess.run(command, cwd=PROJECT_DIR)

    if result.returncode != 0:
        print("ERROR: Command failed.")
        sys.exit(result.returncode)


print("Pulling latest Music Engine files from GitHub...")

# Make sure we are on main
run(["git", "checkout", "main"])

# Pull latest changes
run(["git", "pull", "--ff-only", "origin", "main"])

print("Latest files pulled successfully.")
print()


# Clean previous builds
print("Cleaning previous builds...")

for directory in ("build", "dist"):
    path = PROJECT_DIR / directory

    if path.exists():
        shutil.rmtree(path)


# Build with the Python interpreter running this script
print("Building Music Engine...")

run([
    sys.executable,
    "-m",
    "PyInstaller",
    "--clean",
    "MusicEngine.spec",
])


# Check build exists
dist_dir = PROJECT_DIR / "dist" / "MusicEngine"

if not dist_dir.exists():
    print("ERROR: PyInstaller did not create dist/MusicEngine.")
    sys.exit(1)


print("Build completed successfully.")
print()


# Platform-specific installation directory
if os.name == "nt":
    install_dir = Path(
        os.environ.get("LOCALAPPDATA", Path.home())
    ) / "MusicEngine"
else:
    install_dir = Path.home() / ".local" / "opt" / "MusicEngine"


# Remove previous installation
print(f"Removing previous installation: {install_dir}")

if install_dir.exists():
    shutil.rmtree(install_dir)


# Create installation directory
print(f"Creating installation directory: {install_dir}")

install_dir.mkdir(parents=True, exist_ok=True)


# Copy new build
print("Installing new build...")

shutil.copytree(
    dist_dir,
    install_dir,
    dirs_exist_ok=True,
)


# Linux executable permission
if os.name != "nt":
    executable = install_dir / "MusicEngine"

    if executable.exists():
        executable.chmod(
            executable.stat().st_mode | 0o111
        )


print()
print("Music Engine rebuilt and installed successfully.")
print(f"Installed to: {install_dir}")