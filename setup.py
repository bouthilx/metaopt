#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Installation script for Oríon."""
from glob import iglob
import os
import sys

from setuptools import setup

import versioneer

isfile = os.path.isfile
pjoin = os.path.join
repo_root = os.path.dirname(os.path.abspath(__file__))
mpath = pjoin(repo_root, 'src')
sys.path.insert(0, mpath)


tests_require = [
    'pytest>=3.0.0'
    ]


packages = [
    'orion.core',
    'orion.client',
    'orion.algo',
    ]

setup_args = dict(
    name='orion.core',
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    description='Asynchronous [black-box] Optimization',
    long_description=open(os.path.join(repo_root, "README.rst")).read(),
    license='BSD-3-Clause',
    author=u'Epistímio',
    author_email='xavier.bouthillier@umontreal.ca',
    url='https://github.com/epistimio/orion',
    packages=packages,
    package_dir={'': 'src'},
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'orion = orion.core.cli:main',
            ],
        'OptimizationAlgorithm': [
            'random = orion.algo.random:Random',
            ],
        },
    install_requires=['PyYAML', 'pymongo>=3', 'appdirs', 'numpy', 'scipy'],
    tests_require=tests_require,
    setup_requires=['setuptools', 'appdirs', 'pytest-runner>=2.0,<3dev'],
    extras_require=dict(test=tests_require),
    # "Zipped eggs don't play nicely with namespace packaging"
    # from https://github.com/pypa/sample-namespace-packages
    zip_safe=False
    )

setup_args['keywords'] = [
    'Machine Learning',
    'Deep Learning',
    'Distributed',
    'Optimization',
    ]

setup_args['platforms'] = ['Linux']

setup_args['classifiers'] = [
    'Development Status :: 1 - Planning',
    'Intended Audience :: Developers',
    'Intended Audience :: Education',
    'Intended Audience :: Science/Research',
    'License :: OSI Approved :: BSD License',
    'Operating System :: POSIX',
    'Operating System :: Unix',
    'Programming Language :: Python',
    'Topic :: Scientific/Engineering',
    'Topic :: Scientific/Engineering :: Artificial Intelligence',
] + [('Programming Language :: Python :: %s' % x)
     for x in '3 3.4 3.5 3.6'.split()]

if __name__ == '__main__':
    setup(**setup_args)
