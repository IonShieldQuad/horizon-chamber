# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['H:\\Misc\\horizon\\desktop_app.py'],
    pathex=[],
    binaries=[],
    datas=[('H:\\Misc\\horizon\\static', 'static'), ('H:\\Misc\\horizon\\.env.example', '.'), ('H:\\Misc\\horizon\\requirements.txt', '.')],
    hiddenimports=['aiosqlite', 'dotenv', 'httpx', 'db', 'monitor', 'scheduler', 'deepseek_client', 'feed', 'goal_engine', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HorizonChamber',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['H:\\Misc\\horizon\\static\\horizon_icon.ico'],
)
