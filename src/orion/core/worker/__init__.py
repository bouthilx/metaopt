# -*- coding: utf-8 -*-
"""
:mod:`orion.core.worker` -- Coordination of the optimization procedure
======================================================================

.. module:: worker
   :platform: Unix
   :synopsis: Executes optimization steps and runs training experiment
      with parameter values suggested.

"""
import io
import itertools
import logging
import pprint

from orion.core.io.database import Database
from orion.core.worker.consumer import Consumer
from orion.core.worker.producer import Producer

log = logging.getLogger(__name__)


# TODO: Add automatic curation! But for this we first need to add heartbeat, and for this we should
# convert trials to event-based design. Aww, that won't be done shortly. :(


TOO_MANY_WORKERS = """
The experiment has {number_of_concurrent_workers} workers in execution while \
pool-size is {experiment.pool_size}.
Current worker will be terminated.

To increase the number of workers you can run the following command by replacing \
<POOL-SIZE> with a greater size of your choice:

$ orion hunt --pool-size <POOL-SIZE> --name {experiment.name}
"""


def infer_number_of_concurrent_workers(experiment):
    query = dict(
        experiment=experiment.id,
        status={'$in': ['running', 'reserved']})
    return experiment._db.count('trials', query)


def workon(experiment, worker_trials=None):
    """Try to find solution to the search problem defined in `experiment`."""
    producer = Producer(experiment)
    consumer = Consumer(experiment)

    log.debug("#####  Init Experiment  #####")
    try:
        iterator = range(int(worker_trials))
    except (OverflowError, TypeError):
        # When worker_trials is inf
        iterator = itertools.count()
    for _ in iterator:
        if experiment.is_broken:
            log.error("Search ended due to too many broken trials!!!\nCheck log and database to debug!")
            return 1
        number_of_concurrent_workers = infer_number_of_concurrent_workers(experiment)
        if number_of_concurrent_workers >= experiment.pool_size:
            print(TOO_MANY_WORKERS.format(
                number_of_concurrent_workers=number_of_concurrent_workers,
                experiment=experiment))
            return 0
        log.debug("#### Try to reserve a new trial to evaluate.")
        trial = experiment.reserve_trial(score_handle=producer.algorithm.score)

        if trial is None:
            log.debug("#### Failed to pull a new trial from database.")

            log.debug("#### Fetch most recent completed trials and update algorithm.")
            producer.update()

            log.debug("#### Poll for experiment termination.")
            if experiment.is_done:
                break

            log.debug("#### Produce new trials.")
            producer.produce()

        else:
            log.debug("#### Successfully reserved %s to evaluate. Consuming...", trial)
            consumer.consume(trial)

    stats = experiment.stats
    best = Database().read('trials', {'_id': stats['best_trials_id']})[0]

    stats_stream = io.StringIO()
    pprint.pprint(stats, stream=stats_stream)
    stats_string = stats_stream.getvalue()

    best_stream = io.StringIO()
    pprint.pprint(best['params'], stream=best_stream)
    best_string = best_stream.getvalue()

    log.info("#####  Search finished successfully  #####")
    log.info("\nRESULTS\n=======\n%s\n", stats_string)
    log.info("\nBEST PARAMETERS\n===============\n%s", best_string)
    return 0
