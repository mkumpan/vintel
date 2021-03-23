import sys
from cx_Freeze import setup, Executable
from vi.version import VERSION

# Dependencies are automatically detected, but it might need fine tuning.
build_exe_options = {
        "packages": ['vi', 'sys', 'soupsieve', 'pkgutil', 'certifi', 'distutils'],
        "excludes": ['collections.abc'],
        "include_files": [("vi\ui", "vi\ui")],
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