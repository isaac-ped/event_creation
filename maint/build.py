#!/usr/bin/env python



from argparse import ArgumentParser
import glob
import os
import platform
import shlex
import shutil
from subprocess import check_call
import sys
import yaml

parser = ArgumentParser()
parser.add_argument("--no-clean", action="store_true",
                    help="don't remove existing build dir")
parser.add_argument("--no-build", action="store_true",
                    help="don't build conda packages")
parser.add_argument("--no-convert", action="store_true",
                    help="don't run conda convert")
parser.add_argument("--python", "-p", nargs="+", default=["2.7"],
                    help="python versions to build for (otherwise just 2.7)")
parser.add_argument("--upload", action="store_true")
parser.add_argument("--buildnum","-b",default='0')

def clean():
    """Clean the build directory."""
    print("Removing build dir")
    try:
        shutil.rmtree('build')
        os.mkdir('build')
    except OSError:
        pass


def build(pyver,buildnum):
    """Build a conda package.

    :param str pyver: Python version to build for

    """
    pkg_root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(pkg_root,'conda_environment.yml')) as env_file:
        dependencies = yaml.load(env_file)['dependencies']
    os.environ['DEPENDENCIES'] = '\n'.join('- '+d for d in dependencies)
    os.environ['BUILDNUM'] = buildnum
    build_cmd = [
        "conda", "build",
        "--output-folder=build/",
        "--python", pyver,
    ]

    for chan in ['conda-forge', 'pennmem']:
        build_cmd += ['-c', chan]
    build_cmd += ["conda.recipe"]

    print(' '.join(build_cmd))
    check_call(build_cmd)


def convert():
    """Convert conda packages to other platforms."""
    if sys.platform.startswith('linux'):
        os_name = 'linux'
    elif sys.platform.startswith('win32'):
        os_name = 'win'
    elif sys.platform.startswith('darwin'):
        os_name = 'osx'
    else:
        os_name = 'noarch'
    dirname = '{}-{}'.format(os_name, platform.architecture()[0][:2])
    files = glob.glob('build/{}/*.tar.bz2'.format(dirname))

    for filename in files:
        convert_cmd = "conda convert {} -p all -o build/".format(filename)
        print(convert_cmd)
        check_call(shlex.split(convert_cmd))


def upload():
    """ Upload pre-built package to conda """
    for platform in ['linux-64', 'osx-64', 'win-32', 'win-64']:
        files = glob.glob('build/{}/*.tar.bz2'.format(platform))
        cmds = ['anaconda upload -u pennmem {}'.format(f) for f in files]
        for cmd in cmds:
            print(cmd)
            check_call(shlex.split(cmd))


if __name__ == "__main__":
    args = parser.parse_args()

    if not args.no_clean or not args.no_build:
        clean()

    if not args.no_build:
        for pyver in args.python:
            build(pyver,args.buildnum)

    if not args.no_convert:
        convert()

    if args.upload:
        upload()
