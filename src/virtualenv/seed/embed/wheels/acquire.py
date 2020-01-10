"""Bootstrap"""
from __future__ import absolute_import, unicode_literals

import logging
import os
import sys
from collections import defaultdict
from shutil import copy2
from zipfile import ZipFile

import six

from virtualenv.util.path import Path
from virtualenv.util.subprocess import Popen, subprocess

from . import BUNDLE_SUPPORT, MAX

BUNDLE_FOLDER = Path(__file__).parent


def get_wheels(for_py_version, wheel_cache_dir, extra_search_dir, download, pip, setuptools):
    # not all wheels are compatible with all python versions, so we need to py version qualify it
    packages = {"pip": pip, "setuptools": setuptools}

    # 1. acquire from bundle
    acquire_from_bundle(packages, for_py_version, wheel_cache_dir)
    # 2. acquire from extra search dir
    acquire_from_dir(packages, for_py_version, wheel_cache_dir, extra_search_dir)
    # 3. download from the internet
    if download and packages:
        download_wheel(packages, for_py_version, wheel_cache_dir)

    # in the end just get the wheels
    wheels = _get_wheels(wheel_cache_dir, {"pip": pip, "setuptools": setuptools})
    return {p: next(iter(ver_to_files))[1] for p, ver_to_files in wheels.items()}


def acquire_from_bundle(packages, for_py_version, to_folder):
    for pkg, version in list(packages.items()):
        bundle = get_bundled_wheel(pkg, for_py_version)
        if bundle is not None:
            pkg_version = bundle.stem.split("-")[1]
            exact_version_match = version == pkg_version
            if exact_version_match:
                del packages[pkg]
            if version is None or exact_version_match:
                bundled_wheel_file = to_folder / bundle.name
                if not bundled_wheel_file.exists():
                    logging.debug("get bundled wheel %s", bundle)
                    copy2(str(bundle), str(bundled_wheel_file))


def get_bundled_wheel(package, version_release):
    return BUNDLE_FOLDER / (BUNDLE_SUPPORT.get(version_release, {}) or BUNDLE_SUPPORT[MAX]).get(package)


def acquire_from_dir(packages, for_py_version, to_folder, extra_search_dir):
    if not packages:
        return
    for search_dir in extra_search_dir:
        wheels = _get_wheels(search_dir, packages)
        for pkg, ver_wheels in wheels.items():
            stop = False
            for _, filename in ver_wheels:
                dest = to_folder / filename.name
                if not dest.exists():
                    if wheel_support_py(filename, for_py_version):
                        logging.debug("get extra search dir wheel %s", filename)
                        copy2(str(filename), str(dest))
                        stop = True
                else:
                    stop = True
                if stop and packages[pkg] is not None:
                    del packages[pkg]
                    break


def wheel_support_py(filename, py_version):
    name = "{}.dist-info/METADATA".format("-".join(filename.stem.split("-")[0:2]))
    with ZipFile(six.ensure_text(str(filename)), "r") as zip_file:
        metadata = zip_file.read(name).decode("utf-8")
    marker = "Requires-Python:"
    requires = next(i[len(marker) :] for i in metadata.splitlines() if i.startswith(marker))
    py_version_int = tuple(int(i) for i in py_version.split("."))
    for require in (i.strip() for i in requires.split(",")):
        # https://www.python.org/dev/peps/pep-0345/#version-specifiers
        for operator, check in [
            ("!=", lambda v: py_version_int != v),
            ("==", lambda v: py_version_int == v),
            ("<=", lambda v: py_version_int <= v),
            (">=", lambda v: py_version_int >= v),
            ("<", lambda v: py_version_int < v),
            (">", lambda v: py_version_int > v),
        ]:
            if require.startswith(operator):
                ver_str = require[len(operator) :].strip()
                version = tuple((int(i) if i != "*" else None) for i in ver_str.split("."))[0:2]
                if not check(version):
                    return False
                break
    return True


def _get_wheels(from_folder, packages):
    wheels = defaultdict(list)
    for filename in from_folder.iterdir():
        if filename.suffix == ".whl":
            data = filename.stem.split("-")
            if len(data) >= 2:
                pkg, version = data[0:2]
                if pkg in packages:
                    pkg_version = packages[pkg]
                    if pkg_version is None or pkg_version == version:
                        wheels[pkg].append((version, filename))
    for versions in wheels.values():
        versions.sort(
            key=lambda a: tuple(int(i) if i.isdigit() else i for i in a[0].split(".")), reverse=True,
        )
    return wheels


def download_wheel(packages, for_py_version, to_folder):
    to_download = list(p if v is None else "{}={}".format(p, v) for p, v in packages.items())
    logging.debug("download wheels %s", to_download)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--no-deps",
        "--python-version",
        for_py_version,
        "-d",
        str(to_folder),
    ]
    cmd.extend(to_download)
    # pip has no interface in python - must be a new sub-process
    process = Popen(cmd, env=pip_wheel_env_run("{}{}".format(*sys.version_info[0:2])), stdout=subprocess.PIPE)
    process.communicate()


def pip_wheel_env_run(version):
    env = os.environ.copy()
    env.update(
        {
            six.ensure_str(k): str(v)  # python 2 requires these to be string only (non-unicode)
            for k, v in {
                # put the bundled wheel onto the path, and use it to do the bootstrap operation
                "PYTHONPATH": get_bundled_wheel("pip", version),
                "PIP_USE_WHEEL": "1",
                "PIP_USER": "0",
                "PIP_NO_INPUT": "1",
            }.items()
        }
    )
    return env