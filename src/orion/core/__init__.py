# -*- coding: utf-8 -*-
"""
Oríon is an asynchronous distributed framework for black-box function optimization.

Its purpose is to serve as a hyperparameter optimizer for
machine learning models and training, as well as a flexible experimentation
platform for large scale asynchronous optimization procedures.

It has been designed firstly to disrupt a user's workflow at minimum, allowing
fast and efficient hyperparameter tuning, and secondly to provide secondary APIs
for more advanced features, such as dynamically reporting validation scores on
training time for automatic early stopping or on-the-fly reconfiguration.

Start by having a look here: https://github.com/mila-udem/orion
"""
import os

from appdirs import AppDirs

from orion.core.io.config import Configuration, parse_config_file

from ._version import get_versions

VERSIONS = get_versions()
del get_versions

__descr__ = 'Asynchronous [black-box] Optimization'
__version__ = VERSIONS['version']
__license__ = 'BSD-3-Clause'
__author__ = u'Oríon Team - MILA, Université de Montréal'
__author_short__ = 'MILA'
__author_email__ = 'lisa_labo@iro.umontreal.ca'
__copyright__ = u'2017-2018, Oríon Team - MILA, Université de Montréal'
__authors__ = {
    'tsirif': ('Christos Tsirigotis', 'tsirif@gmail.com'),
    'bouthilx': ('Xavier Bouthillier', 'xavier.bouthillier@gmail.com'),
}
__url__ = 'https://github.com/mila-udem/orion'

DIRS = AppDirs(__name__, __author_short__)
del AppDirs

DEF_CONFIG_FILES_PATHS = [
    os.path.join(DIRS.site_data_dir, 'orion_config.yaml.example'),
    os.path.join(DIRS.site_config_dir, 'orion_config.yaml'),
    os.path.join(DIRS.user_config_dir, 'orion_config.yaml')
    ]

# Default values
# env vars
# Config files
# Command line

# DB only concerns experiment and thus should not affect configuration.


def define_config():
    config = Configuration()
    define_database_config(config)
    define_resources_config(config)
    return config


def define_database_config(config):
    database_config = Configuration()
    database_config.add_option(
        'name', type=str, default='orion', env_var='ORION_DB_NAME')
    database_config.add_option(
        'type', type=str, default='MongoDB', env_var='ORION_DB_TYPE')
    database_config.add_option(
        'host', type=str,
        default='localhost',
        env_var='ORION_DB_ADDRESS')

    config.database = database_config


def define_resources_config(config):
    resource_config = Configuration()
    # TODO: ...
    config.resources = resource_config


def build_config():
    config = define_config()
    for file_path in DEF_CONFIG_FILES_PATHS:
        parse_config_file(file_path, config)

    return config

config = build_config()

# Define config
# database
# resources
# command specific
#     branching

# Set config
# Load global config
# command specific
#     parse config file
#     parse args
