# -*- mode: python ; coding: utf-8 -*-
# IndustrialDefectHost v1.4 打包配置
# 策略：onedir + onnxruntime（.onnx 本地推理）；排除 torch/ultralytics（体积巨量，.pt 推理用源码运行）

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'onnxruntime',
        'numpy',
        'cv2',
        'serial',
        'pymodbus',
        'matplotlib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch',
        'ultralytics',
        'tkinter',
        'PyQt6',
        'PySide6',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='IndustrialDefectHost',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='IndustrialDefectHost',
)
