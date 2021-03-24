import sys
import time

from cx_Freeze import setup, Executable
from vi.version import VERSION, SNAPSHOT
from easy7zip import easy7zip

# Dependencies are automatically detected, but it might need fine tuning.
build_exe_options = {
        "packages": ['vi', 'sys', 'soupsieve', 'pkgutil', 'certifi', 'distutils'],
        "excludes": ['collections.abc'],
        "include_files": [
                ("vi\ui", "vi\ui"),
                ("docs", "docs"),
        ],
        "zip_includes": [
                ("cacert.pem",               "certifi\cacert.pem")
        ],
        "icon": 'icon.ico'
}

# GUI applications require a different base on Windows (the default is for
# a console application).
base = None
if sys.platform == "win32":
    base = "Win32GUI"

setup(  name = "vintel",
        version = VERSION,
        description = "Vintel Remastered",
        options = {
                "build_exe": build_exe_options
        },
        executables = [Executable("vintel.py", base=base)])


print("Building archive")
start = time.time()

zipper = easy7zip()
zipper.AddToArch("dist/vintel-" + VERSION + ("-SNAPSHOT" if SNAPSHOT else  "") + ".7z", "build/exe.win-amd64-2.7")

end = time.time()
print("Time taken: {0} seconds".format(end - start))
