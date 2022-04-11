"""
Build OCP+VTK wheel with shared library dependencies bundled

    *** This proof-of-concept wheel MAY NOT BE DISTRIBUTED ***
    *** as it does not include the requisite license texts ***
    *** of the bundled libraries.                          ***

From the directory containing this file:

    $ python -m build

will build a manylinux wheel into `dist/`.

This setuptools build script works by installing Miniforge and then
OCP into the base conda environment.  Then we copy the installed `OCP`
and `vtk` python package files into a wheel.  This wheel is not
portable as library dependencies are missing, so we use auditwheel to
bundle them into the wheel.

Note that auditwheel is a tool used by many packages to produce
`manylinux` python wheels.  It may be straightforward to use
`delocate` and `delvewheel`, which are similar to auditwheel, to
produce macOS and Windows wheels.

"""

import atexit
import glob
import json
import os.path
import platform
import requests
from setuptools import Extension, setup
import setuptools.command.build_ext
import shutil
import subprocess
import sys
import tempfile
import wheel.bdist_wheel


# The version we will install from conda-forge and
# package into the wheel
OCP_VERSION = "7.5.3.0"


# Where to install Miniforge.
CONDA_PREFIX = tempfile.mkdtemp(prefix="miniforge3-")
atexit.register(shutil.rmtree, CONDA_PREFIX, ignore_errors=True)


def download_miniforge(filename):
    # Download Miniforge.  Url taken from https://github.com/conda-forge/miniforge#downloading-the-installer-as-part-of-a-ci-pipeline
    uname = platform.uname()
    url = f"https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-{uname.system}-{uname.machine}.sh"
    req = requests.get(url)
    req.raise_for_status()
    with open(filename, "wb") as f:
        f.write(req.content)


class copy_installed(setuptools.command.build_ext.build_ext):
    """Build by installing OCP and copying files"""

    def build_extension(self, ext):
        def with_conda(cmd):
            # Run a shell command with conda activated
            subprocess.check_call(f". {CONDA_PREFIX}/bin/activate; {cmd}", shell=True)

        # Install Miniforge to CONDA_PREFIX
        download_miniforge("Miniforge3.sh")
        subprocess.check_call(["bash", "Miniforge3.sh", "-b", "-u", "-p", CONDA_PREFIX])

        # Display info for logging
        with_conda("conda info")

        # Install the correct python version and OCP.
        with_conda(
            f"conda install -y python={sys.version_info.major}.{sys.version_info.minor} ocp={OCP_VERSION}"
        )

        # List packages for logging
        with_conda("conda list")
        with_conda("conda list --explicit")

        # Get the OCP and vtkmodules install locations
        out = subprocess.check_output(
            [
                f"{CONDA_PREFIX}/bin/python",
                "-c",
                "import json, OCP, vtkmodules; print(json.dumps([OCP.__file__, vtkmodules.__file__]))",
            ]
        )
        OCP_file, vtkmodules_file = json.loads(out)

        # self.build_lib is created when packages are copied.  But
        # there are no packages, so we have to create it here.
        os.mkdir(os.path.dirname(self.build_lib))
        os.mkdir(self.build_lib)
        # OCP is a single-file extension; just copy it
        shutil.copy(OCP_file, self.build_lib)
        # vtkmodules is a package; copy it while excluding __pycache__
        assert vtkmodules_file.endswith("/vtkmodules/__init__.py")
        shutil.copytree(
            os.path.dirname(vtkmodules_file),
            os.path.join(self.build_lib, "vtkmodules"),
            ignore=shutil.ignore_patterns("__pycache__"),
        )


class bdist_wheel_repaired(wheel.bdist_wheel.bdist_wheel):
    """bdist_wheel followed by auditwheel-repair"""

    def run(self):
        super().run()
        dist_files = self.distribution.dist_files

        # Exactly one wheel has been created in `self.dist_dir` and
        # recorded in `dist_files`
        [(_, _, bad_whl)] = dist_files
        assert os.path.dirname(bad_whl) == self.dist_dir

        # Conda libraries depend on their location in $conda_prefix because
        # relative RPATHs are used find libraries elsewhere in $conda_prefix
        # (e.g. [$ORIGIN/../../..:$ORIGIN/../../../]).
        #
        # `auditwheel` works by expanding the wheel into a temporary
        # directory and computing the external shared libraries required.
        # But the relative RPATHs are broken, so this fails.  Thankfully,
        # RPATHs all resolve to $conda_prefix/lib, so we can set
        # LD_LIBRARY_PATH to allow `auditwheel` to find them.
        lib_path = os.path.join(CONDA_PREFIX, "lib")

        # Do the repair, placing the repaired wheel into out_dir.
        out_dir = os.path.join(self.dist_dir, "repaired")
        system = platform.system()
        if system == "Linux":
            repair_wheel_linux(lib_path, bad_whl, out_dir)
        elif system == "Darwin":
            repair_wheel_macos(lib_path, bad_whl, out_dir)
        elif system == "Windows":
            repair_wheel_windows(lib_path, bad_whl, out_dir)
        else:
            raise Exception(f"unsupported system {system!r}")

        # Exactly one whl is expected in the dist dir, so delete the
        # bad wheel and move the repaired wheel in.
        [repaired_whl] = glob.glob(os.path.join(out_dir, "*.whl"))
        os.unlink(bad_whl)
        new_whl = os.path.join(self.dist_dir, os.path.basename(repaired_whl))
        shutil.move(repaired_whl, new_whl)
        os.rmdir(out_dir)
        dist_files[0] = dist_files[0][:-1] + (new_whl,)


def repair_wheel_linux(lib_path, whl, out_dir):

    plat = "manylinux_2_31_x86_64"

    args = [
        "env",
        f"LD_LIBRARY_PATH={lib_path}",
        sys.executable,
        "-m",
        "auditwheel",
        "show",
        whl,
    ]
    subprocess.check_call(args)

    args = [
        "env",
        f"LD_LIBRARY_PATH={lib_path}",
        sys.executable,
        "-m",
        "auditwheel",
        "repair",
        f"--plat={plat}",
        f"--wheel-dir={out_dir}",
        whl,
    ]
    subprocess.check_call(args)


def repair_wheel_macos(lib_path, whl, out_dir):

    args = [
        "env",
        f"LD_LIBRARY_PATH={lib_path}",
        os.path.join(sys.prefix, "bin/delocate-listdeps"),
        whl,
    ]
    subprocess.check_call(args)

    # Overwrites the wheel in-place by default
    args = [
        "env",
        f"LD_LIBRARY_PATH={lib_path}",
        os.path.join(sys.prefix, "bin/delocate-wheel"),
        f"--wheel-dir={out_dir}",
        whl,
    ]
    subprocess.check_call(args)


def repair_wheel_windows(lib_path, whl, out_dir):
    args = [sys.executable, "-m", "delvewheel", "show", whl]
    subprocess.check_call(args)
    args = [
        sys.executable,
        "-m",
        "delvewheel",
        "repair",
        f"--wheel-dir={out_dir}",
        whl,
    ]
    subprocess.check_call(args)


setup(
    name="ocpvtk",
    version=OCP_VERSION,
    # Dummy extension to trigger build_ext
    ext_modules=[Extension("__dummy__", sources=[])],
    cmdclass={"bdist_wheel": bdist_wheel_repaired, "build_ext": copy_installed},
)
