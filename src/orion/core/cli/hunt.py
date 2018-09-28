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

from orion.core.cli import base as cli
from orion.core.cli import evc as evc_cli
from orion.core.io import resolve_config
from orion.core.io.evc_builder import EVCBuilder
from orion.core.io.experiment_builder import ExperimentBuilder
from orion.core.worker import workon

log = logging.getLogger(__name__)


def add_subparser(parser):
    """Add the subparser that needs to be used for this command"""
    hunt_parser = parser.add_parser('hunt', help='hunt help')

    orion_group = cli.get_basic_args_group(hunt_parser)

    orion_group.add_argument(
        '--max-trials', type=int, metavar='#',
        help="number of trials to be completed for the experiment. This value "
             "will be saved within the experiment configuration and reused "
             "across all workers to determine experiment's completion. "
             "(default: %s)" % resolve_config.DEF_CMD_MAX_TRIALS[1])

    orion_group.add_argument(
        '--worker-trials', type=int, metavar='#',
        help="number of trials to be completed for this worker. "
             "If the experiment is completed, the worker will die even if it "
             "did not reach its maximum number of trials "
             "(default: %s)" % resolve_config.DEF_CMD_WORKER_TRIALS[1])

    orion_group.add_argument(
        "--pool-size", type=int, metavar='#',
        help="number of concurrent workers to evaluate candidate samples "
             "(default: %s)" % resolve_config.DEF_CMD_POOL_SIZE[1])

    orion_group.add_argument(
        "--max-broken", type=int, metavar='#',
        help="maximum number of broken trials to be tolerated before declaring "
             "experiment as broken (default: %s)" % resolve_config.DEF_CMD_MAX_TRIALS[1])

    evc_cli.get_branching_args_group(hunt_parser) 
    cli.get_user_args_group(hunt_parser)

    hunt_parser.set_defaults(func=main)

    return hunt_parser


def main(args):
    """Build experiment and execute hunt command"""
    args['root'] = None
    args['leafs'] = []
    # TODO: simplify when parameter parsing is refactored
    worker_trials = ExperimentBuilder().fetch_full_config(args)['worker_trials']
    experiment = EVCBuilder().build_from(args)
    workon(experiment, worker_trials)
