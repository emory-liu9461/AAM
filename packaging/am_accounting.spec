# PyInstaller build spec for Aedalus Nexus.
#   pyinstaller packaging/am_accounting.spec --noconfirm
# Produces dist/Aedalus Nexus/ (a folder with "Aedalus Nexus.exe" on Windows).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from am_accounting import __version__  # noqa: E402

datas = [(str(ROOT / "am_accounting" / "assets"), "am_accounting/assets")]
datas += collect_data_files("pdfplumber")
datas += collect_data_files("pdfminer")
datas += collect_data_files("rapidocr_onnxruntime")  # OCR models and configs
datas += collect_data_files("pypdfium2_raw") + collect_data_files("pypdfium2")
hiddenimports = (collect_submodules("am_accounting") + collect_submodules("pdfminer")
                 + collect_submodules("rapidocr_onnxruntime") + ["pypdfium2", "pypdfium2_raw", "onnxruntime"])

# Qt modules the app never uses: leave them out to keep the download small.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtNetworkAuth", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "tkinter", "pytest", "reportlab",
    # Pulled in only by optional code paths (openpyxl pandas/lxml support).
    "pandas", "lxml", "matplotlib", "IPython",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

version_file = None
if sys.platform == "win32":
    version_file = str(ROOT / "build" / "version_info.txt")
    Path(version_file).parent.mkdir(exist_ok=True)
    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    nums = ", ".join(parts)
    Path(version_file).write_text(f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums})),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Aedalus'),
      StringStruct('FileDescription', 'Aedalus Nexus'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductName', 'Aedalus Nexus'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Aedalus Nexus",
    console=False,
    icon=str(ROOT / "am_accounting" / "assets" / "app.ico"),
    version=version_file,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Aedalus Nexus")
