# PyInstaller-Spezifikation für Windows: ein Ordner mit llmanno.exe (Oberfläche) und
# llmanno-rerun.exe (Konsole). Bauen mit:  uv run pyinstaller packaging/llmanno.spec --noconfirm
import shutil

from PyInstaller.utils.hooks import copy_metadata

ROOT = SPECPATH + "/.."
# Übersetzungen liegen neben dem Paket, importlib.metadata braucht die Paket-Metadaten.
datas = [(ROOT + "/src/llmanno/i18n/*.qm", "llmanno/i18n")]
# Beispieldaten für „Demo öffnen“
datas += [(ROOT + "/src/llmanno/demo/*", "llmanno/demo")]
datas += [(ROOT + "/docs/" + name, "llmanno/help") for name in ("ANLEITUNG.md", "MANUAL.md")]  # F1
# Rekursiv: environment.txt im Replikationspaket nennt sonst scipy, pandas, httpx … nicht.
datas += copy_metadata("llmanno", recursive=True)
excludes = ["pytest", "sklearn", "statsmodels", "matplotlib", "IPython", "tkinter"]


def analysis(script):
    return Analysis(
        [script], pathex=[ROOT + "/src"], datas=datas, excludes=excludes,
        hiddenimports=["keyring.backends.Windows"],
    )


gui = analysis(ROOT + "/packaging/launcher.py")
cli = analysis(ROOT + "/packaging/rerun_launcher.py")
gui_exe = EXE(
    PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="llmanno", console=False
)
cli_exe = EXE(
    PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="llmanno-rerun", console=True
)
COLLECT(
    gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="llmanno"
)

# Sichtbar neben den Programmen statt in _internal: Beispielprojekt und Lizenzen. llmanno steht
# unter der AGPL; Qt/PySide unter der LGPL samt GPL (deren Wheel bringt nur den kommerziellen
# Text mit); die übrigen Pakete haben ihre Texte in den *.dist-info-Ordnern unter _internal.
TARGET = DISTPATH + "/llmanno"
shutil.copytree(ROOT + "/src/llmanno/demo", TARGET + "/Beispiel", dirs_exist_ok=True)
shutil.copytree(ROOT + "/packaging/licenses", TARGET + "/Lizenzen", dirs_exist_ok=True)
shutil.copyfile(ROOT + "/LICENSE", TARGET + "/LICENSE.txt")
