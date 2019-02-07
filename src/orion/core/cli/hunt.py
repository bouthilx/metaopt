#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
:mod:`orion.core.cli.hunt` -- Module running the optimization command
=====================================================================

.. module:: hunt
   :platform: Unix
   :synopsis: Gets an experiment and iterates over it until one of the exit conditions is met

"""

import logging

from numpy import inf as infinity

import orion
from orion.core.cli import base as cli
from orion.core.cli import evc as evc_cli
from orion.core.io import resolve_config
from orion.core.io.evc_builder import EVCBuilder
from orion.core.worker import workon

log = logging.getLogger(__name__)


def add_subparser(parser):
    """Add the subparser that needs to be used for this command"""
    hunt_parser = parser.add_parser('hunt', help='hunt help')

    orion_group = cli.get_basic_args_group(hunt_parser)

    orion_group.add_argument(
        '--max-trials', type=int, metavar='#', default=infinity,
        help="number of jobs/trials to be completed "
             "(default: inf/until preempted)" % )

    orion_group.add_argument(
        "--pool-size", type=int, metavar='#', default=10
        help="number of concurrent workers to evaluate candidate samples "
             "(default: 10)")

    orion_group.add_argument(
        "--algorithms", type=str,
        help=("Algorithm name with default setting. Use configuration file to define "
              "specific settings of algorithms."))

    evc_cli.get_branching_args_group(hunt_parser)

    cli.get_user_args_group(hunt_parser)

    hunt_parser.set_defaults(func=main)
    hunt_parser.set_defaults(config_parser=set_command_config)

    return hunt_parser


def set_command_config(config):
    evc_cli.define_branching_config(config)


def main(args):
    """Build experiment and execute hunt command"""
    args['root'] = None
    args['leafs'] = []
    experiment = EVCBuilder().build_from(args)
    workon(experiment)
