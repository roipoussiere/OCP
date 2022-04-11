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

import glob
import json
import os
import os.path
import platform
from setuptools import Extension, setup
import setuptools.command.build_ext
import subprocess
import sys
import wheel.bdist_wheel
import shutil


# The version we will install from conda-forge and
# package into the wheel
OCP_VERSION="7.5.3.0"


# Where to install Miniforge.
CONDA_PREFIX = os.path.expanduser("~/miniforge3")


class copy_installed(setuptools.command.build_ext.build_ext):
    """Build by installing OCP and copying files"""

    def build_extension(self, ext):

        # Download Miniforge.  Taken from https://github.com/conda-forge/miniforge#downloading-the-installer-as-part-of-a-ci-pipeline
        cmd = 'wget -O Miniforge3.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"'
        subprocess.check_call(['sh', '-c', cmd])

        # Install Miniforge to $HOME/miniforge3
        subprocess.check_call(["bash", "Miniforge3.sh", "-b", "-u", "-p", CONDA_PREFIX])

        # Install the correct python version and OCP.
        cmd = f"conda install -y python={sys.version_info.major}.{sys.version_info.minor} ocp={OCP_VERSION}"
        subprocess.check_call(f". {CONDA_PREFIX}/bin/activate; {cmd}", shell=True)

        # Get the OCP and vtkmodules install locations
        out = subprocess.check_output([
            f"{CONDA_PREFIX}/bin/python",
            "-c",
            "import json, OCP, vtkmodules; print(json.dumps([OCP.__file__, vtkmodules.__file__]))"])
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

        system = platform.system()
        if system == "Linux":
            # Do the repair, placing the manylinux wheel into the same
            # directory as the linux whl
            plat = "manylinux_2_31_x86_64"
            manylinux_whl = repair_wheel_linux(lib_path, bad_whl, plat, self.dist_dir)

            # Only one whl is expected, so keep only the manylinux wheel
            # on disk, and replace the linux wheel with the manylinux
            # wheel in `dist_files`, and
            os.remove(bad_whl)
            dist_files[0] = dist_files[0][:-1] + (manylinux_whl,)

        elif system == "MacOS":
            # The repaired wheel will overwrite the broken wheel
            repair_wheel_macos(lib_path, bad_whl)

        else:
            raise Exception(f"unsupported system {system!r}")


def repair_wheel_linux(lib_path, whl, plat, out_dir):

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

    [repaired] = glob.glob(os.path.join(out_dir, f"*-manylinux*.whl"))
    return repaired



def repair_wheel_macos(lib_path, whl):

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
        whl,
    ]
    subprocess.check_call(args)

    return whl


setup(
    name="ocpvtk",
    version=OCP_VERSION,
    # Dummy extension to trigger build_ext
    ext_modules=[Extension("__dummy__", sources=[])],
    cmdclass={"bdist_wheel": bdist_wheel_repaired, "build_ext": copy_installed},
)
