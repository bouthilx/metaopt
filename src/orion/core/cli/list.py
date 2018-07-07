#!/usr/bin/env python
# -*- coding: utf-8 -*-
# pylint: disable=eval-used,protected-access
"""
:mod:`orion.core.cli.list` -- Module to list experiments
========================================================

.. module:: list 
   :platform: Unix
   :synopsis: List experiments in termnial

"""
import collections
import logging
import os
import re

from orion.core.cli import base as cli
from orion.core.io.convert import infer_converter_from_file_type
from orion.core.io.experiment_builder import ExperimentBuilder
from orion.core.utils.format_trials import tuple_to_trial

log = logging.getLogger(__name__)


def add_subparser(parser):
    """Add the subparser that needs to be used for this command"""
    insert_parser = parser.add_parser('insert', help='insert help')

    cli.get_basic_args_group(insert_parser)

    insert_parser.set_defaults(func=main)

    return insert_parser


def main(args):
    """Fetch config and insert new point"""
    local_config = ExperimentBuilder().fetch_full_config(args, use_db=False)
    db_opts = local_config['database']
    dbtype = db_opts.pop('type')

    database = build_database(dbtype, **db_opts)

    selection = {
        'name': 1,
        'refers.root_id': 1
    }
    experiments = database.read("experiments", {})
    root_experiments = [e for e in experiments if e['refers']['root_id'] == e['_id']]
    trees = []
    for root_experiment in root_experiments:
        trees.append(build_experiment_tree(root_experiment['name']))

    print("\n".join(pprint.pformat(tree) for tree in trees))


def build_database(dbtype, **db_opts):
    # Information should be enough to infer experiment's name.
    log.debug("Creating %s database client with args: %s", dbtype, db_opts)
    try:
        return Database(of_type=dbtype, **db_opts)
    except ValueError:
        if Database().__class__.__name__.lower() != dbtype.lower():
            raise


def build_experiment_tree(name):
    return _build_experiment_tree(EVCBuilder().build_view_from(name)._node)


def _build_experiment_tree(node):
    children = {}
    for child in experiment_node.children:
        children[child.name] = _build_experiment_tree(child)

    return {node.name: children}
