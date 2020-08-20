# -*- coding: utf-8 -*-
"""
:mod:`orion.serving.responses` -- Helpers for building responses according to the specification
===============================================================================================

.. module:: responses
   :platform: Unix
   :synopsis: Offers functions and attributes to generate response objects according
      to the API specification
"""
from orion.core.worker.experiment import Experiment
from orion.core.worker.trial import Trial

ERROR_EXPERIMENT_NOT_FOUND = 'Experiment not found'
ERROR_INVALID_PARAMETER = 'Invalid parameter'
ERROR_TRIAL_NOT_FOUND = 'Trial not found'


def build_trial_response(trial: Trial) -> dict:
    """
    Build the response representing a trial according to the API specification.

    Parameters
    ----------
    trial: Trial
        The trial to return for the API.

    Returns
    -------
    A JSON-serializable dict representing the given trial.

    """
    return {'id': trial.id,
            'submitTime': str(trial.submit_time),
            'startTime': str(trial.start_time),
            'endTime': str(trial.end_time),
            'parameters': trial.params,
            'objective': trial.objective.value,
            'statistics': {statistic.name: statistic.value for statistic in trial.statistics}}


def build_experiment_response(experiment: Experiment,
                              status: str,
                              algorithm: dict,
                              best_trial: Trial = None):
    """
    Build the response representing an experiment response object according to the API
    specification.

    Parameters
    ----------
    experiment: Experiment
        The experiment to return to the API
    status: str
        The status of the experiment
    algorithm: dict
        The dictionary containing the algorithm's configuration
    best_trial: Trial (Optional)
        The best trial to date of the experiment

    Returns
    -------
    A JSON-serializable experiment response object representing the given experiment.
    """
    return {
        "name": experiment.name,
        "version": experiment.version,
        "status": status,
        "trialsCompleted": experiment.stats['trials_completed'] if experiment.stats else 0,
        "startTime": str(experiment.stats['start_time']) if experiment.stats else None,
        "endTime": str(experiment.stats['finish_time']) if experiment.stats else None,
        "user": experiment.metadata['user'],
        "orionVersion": experiment.metadata['orion_version'],
        "config": {
            "maxTrials": experiment.max_trials,
            "poolSize": experiment.pool_size,
            "algorithm": algorithm,
            "space": experiment.configuration['space']
        },
        "bestTrial": build_trial_response(best_trial) if best_trial else {}
    }
