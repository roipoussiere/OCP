"""
Build OCP+VTK wheel with shared library dependencies bundled

    *** This proof-of-concept wheel MAY NOT BE DISTRIBUTED ***
    *** as it does not include the requisite license texts ***
    *** of the bundled libraries.                          ***

From the directory containing this file, and with an appropriate conda
environment activated:

    $ python -m build --no-isolation

will build a manylinux wheel into `dist/`.

A conda environment with `OCP` (and all its dependencies, including
`vtk`), `auditwheel`, and `build` (the PEP 517 compatible Python
package builder) python packages is required, as well as the
`patchelf` binary.  Note that the vtk package needs to be bundled to
avoid multiple copies of the VTK shared libraries, which appears to
cause errors.

This setuptools build script works by first adding the installed `OCP`
and `vtk` python package files into a wheel.  This wheel is not
portable as library dependencies are missing, so we use auditwheel to
bundle them into the wheel.

Note that auditwheel is a tool used by many packages to produce
`manylinux` python wheels.  It may be straightforward to use
`delocate` and `delvewheel`, which are similar to auditwheel, to
produce macOS and Windows wheels.

"""

import OCP
import glob
import json
import os
import os.path
from setuptools import Extension, setup
import setuptools.command.build_ext
import subprocess
import sys
import vtkmodules
import wheel.bdist_wheel
import shutil


class copy_installed(setuptools.command.build_ext.build_ext):
    """Build by copying files installed by conda"""

    def build_extension(self, ext):
        # self.build_lib is created when packages are copied.  But
        # there are no packages, so we have to create it here.
        os.mkdir(os.path.dirname(self.build_lib))
        os.mkdir(self.build_lib)
        # OCP is a single-file extension; just copy it
        shutil.copy(OCP.__file__, self.build_lib)
        # vtkmodules is a package; copy it while excluding __pycache__
        assert vtkmodules.__file__.endswith("/vtkmodules/__init__.py")
        shutil.copytree(
            os.path.dirname(vtkmodules.__file__),
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
        [(_, _, linux_whl)] = dist_files
        assert os.path.dirname(linux_whl) == self.dist_dir

        # Do the repair, placing the manylinux wheel into the same
        # directory as the linux whl
        plat = "manylinux_2_31_x86_64"
        manylinux_whl = repair_wheel(linux_whl, plat, self.dist_dir)

        # Only one whl is expected, so keep only the manylinux wheel
        # on disk, and replace the linux wheel with the manylinux
        # wheel in `dist_files`, and
        os.remove(linux_whl)
        dist_files[0] = dist_files[0][:-1] + (manylinux_whl,)


def repair_wheel(whl, plat, out_dir):
    # Conda libraries depend on their location in $conda_prefix because
    # relative RPATHs are used find libraries elsewhere in $conda_prefix
    # (e.g. [$ORIGIN/../../..:$ORIGIN/../../../]).
    #
    # `auditwheel` works by expanding the wheel into a temporary
    # directory and computing the external shared libraries required.
    # But the relative RPATHs are broken, so this fails.  Thankfully,
    # RPATHs all resolve to $conda_prefix/lib, so we can set
    # LD_LIBRARY_PATH to allow `auditwheel` to find them.

    lib = os.path.join(conda_prefix, "lib")
    args = [
        "env",
        f"LD_LIBRARY_PATH={lib}",
        sys.executable,
        "-m",
        "auditwheel",
        "show",
        whl,
    ]
    subprocess.check_call(args)

    args = [
        "env",
        f"LD_LIBRARY_PATH={lib}",
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


# Get the metadata for conda and the `ocp` package.
args = ["conda", "info", "--json"]
info = json.loads(subprocess.check_output(args))
conda_prefix = info["conda_prefix"]
args = ["conda", "list", "--json", "^ocp$"]
[ocp_meta] = json.loads(subprocess.check_output(args))

setup(
    name="ocpvtk",
    version=ocp_meta["version"],
    # Dummy extension to trigger build_ext
    ext_modules=[Extension("__dummy__", sources=[])],
    cmdclass={"bdist_wheel": bdist_wheel_repaired, "build_ext": copy_installed},
)
