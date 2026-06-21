"""Build Horizon Chamber as a standalone Windows executable.

Usage:
    python build.py                   # build .exe in dist/
    python build.py --onefile         # build single-file .exe (larger but portable)
    python build.py --debug           # keep console window for debugging

Requires: pyinstaller, pywebview (pip install --user pyinstaller pywebview)
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Horizon Chamber executable")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Bundle into a single .exe (slower start, larger size)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Keep console window visible (useful for diagnosing crashes)",
    )
    parser.add_argument(
        "--name",
        default="HorizonChamber",
        help="Output executable name (default: HorizonChamber)",
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    dist_dir = here / "dist"

    # Determine which spec file to use
    spec_name = "horizon_chamber_debug.spec" if args.debug else "horizon_chamber.spec"

    # Clean previous build artifacts
    for d in [here / "build", dist_dir]:
        if d.exists():
            shutil.rmtree(d)

    print(f"🔨 Building {'single-file' if args.onefile else 'folder'} executable …")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        args.name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(here / "build"),
        "--specpath",
        str(here),
    ]

    if args.onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    if not args.debug:
        cmd.append("--noconsole")  # no console window in production

    # App icon for the executable and taskbar
    icon_path = here / "static" / "horizon_icon.ico"
    if icon_path.exists():
        cmd.extend(["--icon", str(icon_path)])

    # Add data files: static folder, .env template, db schema
    cmd.extend(["--add-data", f"{here / 'static'}{';'}static"])
    cmd.extend(["--add-data", f"{here / '.env'}{';'}."])
    cmd.extend(["--add-data", f"{here / 'requirements.txt'}{';'}."])

    # Hidden imports (PyInstaller auto-detect may miss some)
    cmd.extend(["--hidden-import", "aiosqlite"])
    cmd.extend(["--hidden-import", "dotenv"])
    cmd.extend(["--hidden-import", "httpx"])
    cmd.extend(["--hidden-import", "db"])
    cmd.extend(["--hidden-import", "monitor"])
    cmd.extend(["--hidden-import", "scheduler"])
    cmd.extend(["--hidden-import", "deepseek_client"])
    cmd.extend(["--hidden-import", "uvicorn.logging"])
    cmd.extend(["--hidden-import", "uvicorn.loops.auto"])
    cmd.extend(["--hidden-import", "uvicorn.protocols.http.auto"])
    cmd.extend(["--hidden-import", "uvicorn.protocols.websockets.auto"])

    cmd.append(str(here / "desktop_app.py"))

    print("   " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=here)
    if result.returncode != 0:
        print("❌ Build failed")
        sys.exit(result.returncode)

    print(f"\n✅ Build complete!")
    print(f"   📁 Output: {dist_dir / args.name}")
    if args.onefile:
        print(f"   📄 Executable: {dist_dir / args.name / f'{args.name}.exe'}")
    else:
        print(f"   📄 Executable: {dist_dir / f'{args.name}.exe'}")

    print("\n💡 Run with:  dist\\HorizonChamber.exe")
    print("   Or double-click the .exe in Explorer.\n")


if __name__ == "__main__":
    main()
