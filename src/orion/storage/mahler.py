# -*- coding: utf-8 -*-
"""
:mod:`orion.storage.mahler -- Mahler Storage Protocol
=====================================================

.. module:: base
   :platform: Unix
   :synopsis: Implement a storage protocol to allow Orion to use mahler as a storage method

"""

from collections import defaultdict
import copy
import datetime
import hashlib
import logging
import sys
import warnings

import bson.codec_options

from orion.core.io.database import DuplicateKeyError
from orion.core.utils.flatten import flatten, unflatten
from orion.storage.base import BaseStorageProtocol, FailedUpdate, MissingArguments

import pytz


log = logging.getLogger(__name__)


# TODO: Remove this when factory is reworked
class Mahler:    # noqa: F811
    """Forward declaration because of a weird factory bug where Mahler is not found"""

    def __init__(self, uri):
        assert False, 'This should not be called'


HAS_MAHLER = False
REASON = None
try:
    import mahler.client as mahler
    import mahler.core.status as mahler_status_module
    HAS_MAHLER = True
except ImportError as e:
    REASON = 'Mahler is not installed'
    mahler = None
    # raise


def OrionTrial(**kwargs):
    # NOTE: To fix circular import...
    from orion.core.worker.trial import Trial
    kwargs.pop('hash_params')
    return Trial(**kwargs)

MAHLER_TO_ORION_STATUS = {
    'OnHold': 'interrupted',
    'Queued': 'interrupted',
    'Reserved': 'reserved',
    'Running': 'reserved',
    'Broken': 'broken',
    'FailedOver': 'interrupted',
    'SwitchedOver': 'interrupted',
    'Interrupted': 'interrupted',
    'Suspended': 'suspended',
    'Cancelled': 'broken',
    'Acknowledged': 'broken',
    'Completed': 'completed'}

def instantiate_mahler_status(mahler_status):
    return getattr(mahler_status_module, mahler_status)('')


ORION_TO_MAHLER_STATUS = {}

for mahler_status, orion_status in MAHLER_TO_ORION_STATUS.items():
    if orion_status not in ORION_TO_MAHLER_STATUS:
        ORION_TO_MAHLER_STATUS[orion_status] = []

    ORION_TO_MAHLER_STATUS[orion_status].append(instantiate_mahler_status(mahler_status))


def convert_orion_status(status):
    """Convert orion status to list of corresponding mahler status
    
    This should only be used for queries."""
    list_of_status = []
    if isinstance(status, str):
        status = [status]
    for single_status in status:
        if single_status in ORION_TO_MAHLER_STATUS:
            list_of_status += ORION_TO_MAHLER_STATUS[single_status]
        elif single_status in MAHLER_TO_ORION_STATUS:
            list_of_status.append(instantiate_mahler_status(single_status))
        else:
            raise ValueError(f'Unknown status: {single_status}')

    return list_of_status


def convert_mahler_status(status):
    """Convert track status to orion status"""
    return MAHLER_TO_ORION_STATUS[status]


def experiment_uid(name, version, tags):
    """Return an experiment uid from its name and version for Mahler"""
    sha = hashlib.sha256()
    sha.update(name.encode('utf8'))
    sha.update(bytes([version]))
    for tag in tags:
        sha.update(tag.encode('utf8'))
    return sha.hexdigest()


class TrialAdapter:
    """Mock Trial, see `~orion.core.worker.trial.Trial`

    Parameters
    ----------
    task
        Mahler task object

    orion_trial
        Orion trial object

    objective: str
        objective key

    """

    def __init__(self, task, orion_trial=None, objective=None):
        self.task = task
        if orion_trial is None:
            orion_trial = OrionTrial(**task.attributes)
        self.memory = orion_trial
        self.session_group = None
        self.objective_key = objective
        self.objectives_values = None
        self._results = []

    def __deepcopy__(self, memo=None):
        # NOTE: Ugly hack to get lying_trials working for now...
        #       Problem is we cant edit results of Mahler Trial and this is necessary for a lying
        #       trials. On the other end we don't need the Mahler Trial mechanism for the lying
        #       trial, so maybe that is just the best we can do.
        # TODO: Try to solve this.
        # config = self.task.attributes
        # return OrionTrial(id=config['id'], params=config['params'], results=[])
        return copy.deepcopy(self.memory)

    def _repr_values(self, values, sep=','):
        """Represent with a string the given values."""
        return

    def __str__(self):
        """Represent partially with a string."""
        param_rep = ','.join(map(lambda value: "{0.name}:{0.value}".format(value), self._params))
        ret = "TrialAdapter(uid={3}, experiment={0}, status={1}, params={2})".format(
            repr(self.experiment[:10]), repr(self.status), param_rep, self.task.id)
        return ret

    __repr__ = __str__

    @property
    def experiment(self):
        """See `~orion.core.worker.trial.Trial`"""
        if self.memory is not None:
            return self.memory.experiment
        return self.task.attributes['experiment']

    @property
    def hearbeat(self):
        """See `~orion.core.worker.trial.Trial`"""
        return self.task.updated_on

    @property
    def id(self):
        """See `~orion.core.worker.trial.Trial`"""
        return self.task.attributes['_id']

    @property
    def params(self):
        """See `~orion.core.worker.trial.Trial`"""
        if self.memory is not None:
            return self.memory.params

        return unflatten({param.name: param.value for param in self._params})

    @property
    def _params(self):
        """See `~orion.core.worker.trial.Trial`"""
        if self.memory is not None:
            return self.memory._params

        return [OrionTrial.Param(**param_dict) for param_dict in self.task.attributes['params']]

    @property
    def status(self):
        """See `~orion.core.worker.trial.Trial`"""
        # if self.memory is not None:
        #     return self.memory.status
        return convert_mahler_status(self.task.status.name)

    @status.setter
    def status(self, value):
        """See `~orion.core.worker.trial.Trial`"""
        if self.memory is not None:
            self.memory.status = value

    def to_dict(self):
        """See `~orion.core.worker.trial.Trial`"""
        trial = {
            '_id': self.task.attributes['_id'],
            'results': [r.to_dict() for r in self.results],
            'params': [p.to_dict() for p in self._params],
            'heartbeat': self.hearbeat,
            'submit_time': self.submit_time,
            'end_time': self.end_time,
            'experiment': self.experiment,
            'status': self.status
        }

        return trial

    @property
    def lie(self):
        """See `~orion.core.worker.trial.Trial`"""
        # we do not lie like Orion does
        return None

    @property
    def objective(self):
        """See `~orion.core.worker.trial.Trial`"""
        from orion.core.worker.trial import Trial as OrionTrial

        def result(val):
            return OrionTrial.Result(name=self.objective_key, value=val, type='objective')

        if self.objective_key is None:
            raise RuntimeError('no objective key was defined!')

        if self.status != 'completed' or self.task.output is None:
            return None

        return result(flatten(self.task.output)[self.objective_key])

    @property
    def results(self):
        """See `~orion.core.worker.trial.Trial`"""
        from orion.core.worker.trial import Trial as OrionTrial

        self._results = []

        if self.task.output is None:
            return []

        # TODO: This won't support sub-dict objective-key
        for k, values in self.task.output.items():
            result_type = 'statistic'
            if k == self.objective_key:
                result_type = 'objective'

            if isinstance(values, dict):
                items = list(values.items())
                items.sort(key=lambda v: v[0])

                val = items[-1][1]
                self._results.append(OrionTrial.Result(name=k, type=result_type, value=val))
            elif isinstance(values, list):
                self._results.append(OrionTrial.Result(name=k, type=result_type, value=values[-1]))

        return self._results

    @property
    def hash_params(self):
        """See `~orion.core.worker.trial.Trial`"""
        from orion.core.worker.trial import Trial as OrionTrial
        return OrionTrial.compute_trial_hash(self, ignore_fidelity=True)

    @results.setter
    def results(self, value):
        """See `~orion.core.worker.trial.Trial`"""
        pass
        # self._results = value

    @property
    def gradient(self):
        """See `~orion.core.worker.trial.Trial`"""
        return None

    @property
    def submit_time(self):
        """See `~orion.core.worker.trial.Trial`"""
        return self.task.created_on

    @property
    def end_time(self):
        """See `~orion.core.worker.trial.Trial`"""
        return self.task.stopped_on

    @end_time.setter
    def end_time(self, value):
        """See `~orion.core.worker.trial.Trial`"""
        pass

    @property
    def heartbeat(self):
        """Trial Heartbeat"""
        return self.end_time

    @property
    def parents(self):
        """See `~orion.core.worker.trial.Trial`"""
        return self.task.attributes['parents']

    @parents.setter
    def parents(self, other):
        """See `~orion.core.worker.trial.Trial`"""
        pass


def hpo_master(name, version=None, space=None, algorithms=None,
               strategy=None, max_trials=None, storage=None, branching=None,
               working_dir=None):

    experiment = create_experiment(
        name, version=None, space=None, algorithms=None, strategy=None, max_trials=None,
        storage=None, branching=None, working_dir=None)
    
    # TODO

EXPERIMENT_TAG = 'orion-experiment'
TRIAL_TAG = 'orion-trial'
ORION_TAGS = [EXPERIMENT_TAG, TRIAL_TAG]


class Mahler(BaseStorageProtocol):   # noqa: F811
    """Implement a generic protocol to allow Orion to communicate using
    different storage backend

    Only supports python API.
    """

    def __init__(self, hpo_operator, operator, objective, client=None, container=None, tags=None,
                 ignore_tags=None):
        self.hpo_operator = hpo_operator
        # TODO: Operator of trial may require special resources.usage
        #       or that can be fixed at the creation of Mahler() storage since it should be shared
        #       by all trials, but different for experiments.
        self.operator = operator
        if client is None:
            client = mahler.Client()
        self.client = client
        self.client.registrar._db._db = self.client.registrar._db._db.with_options(
            codec_options=bson.codec_options.CodecOptions(
                tz_aware=True,
                tzinfo=pytz.UTC))
        self.objective = objective
        self.container = container
        if tags is None:
            tags = []
        else:
            tags = [tag for tag in tags if tag not in ORION_TAGS]
        self.tags = tags
        if ignore_tags is None:
            ignore_tags = []
        else:
            ignore_tags = [tag for tag in ignore_tags if tag not in ORION_TAGS]
        self.ignore_tags = ignore_tags
        self.lies = dict()
        assert self.objective is not None, 'An objective should be defined!'

    def create_experiment(self, config):
        """Insert a new experiment inside the database"""

        if self.fetch_experiments(dict(name=config['name'], version=config.get('version', 1))):
            raise DuplicateKeyError('Experiment was already created')

        tags = [EXPERIMENT_TAG, f'{config["name"]}-v{config["version"]}'] + self.tags

        config.setdefault('_id', experiment_uid(config['name'], config['version'], tags))

        task = self.client.register(
            self.hpo_operator.delay(),
            container=self.container,
            tags=tags,
            attributes=config)

        return config

    def update_experiment(self, experiment=None, uid=None, where=None, **kwargs):
        """See :func:`~orion.storage.BaseStorageProtocol.update_experiment`"""

        log.warning('Cannot update experiments with Mahler')

        # NOTE:
        # This makes it impossible to update refers dict properly
        # Not possible to update max_trials.

        return None

    def fetch_experiments(self, query, selection=None):
        """Fetch all experiments that match the query"""

        mahler_query = dict(
            tags=[EXPERIMENT_TAG] + self.tags)

        if query:
            mahler_query['attributes'] = query

        exp_tasks = self.client.find(**mahler_query)


        exp_tasks = list(exp_tasks)

        experiments = [exp.attributes for exp in exp_tasks if not self._should_ignore(exp.tags)]

        # TODO: Support selection
        return experiments

    def _should_ignore(self, tags):
        return len(set(self.ignore_tags) & set(tags))

    def register_trial(self, trial):
        """Create a new trial to be executed"""

        # Verify if trial already exists
        if self.get_trial(uid=trial.id):
            raise DuplicateKeyError(f'Trial {trial.id} already exists')

        experiments = self.fetch_experiments(dict(_id=trial.experiment))
        if not experiments:
            raise ValueError(f'Experiment does not exist: {trial.experiment}')
        experiment = experiments[0]
        
        attributes = trial.to_dict()
        attributes['hash_params'] = trial.hash_params

        task = self.client.register(
            self.operator.delay(**trial.params),
            container=self.container,
            tags=[TRIAL_TAG, f'{experiment["name"]}-v{experiment["version"]}'] + self.tags,
            attributes=attributes
        )

        return TrialAdapter(task, trial, objective=self.objective)

    def register_lie(self, trial):
        """Register a *fake* trial created by the strategist.

        The main difference between fake trial and original ones is the addition of a fake objective
        result, and status being set to completed. The id of the fake trial is different than the id
        of the original trial, but the original id can be computed using the hashcode on parameters
        of the fake trial. See mod:`orion.core.worker.strategy` for more information and the
        Strategist object and generation of fake trials.

        Parameters
        ----------
        trial: `Trial` object
            Fake trial to register in the database

        """
        warnings.warn('Mahler does not persist lies!')

        if trial.id in self.lies:
            raise DuplicateKeyError('Lie already exists')

        self.lies[trial.id] = trial
        return trial

    def _fetch_trials(self, query, *args, **kwargs):
        """Fetch all the trials that match the query"""
        def sort_key(item):
            submit_time = item.submit_time
            if submit_time is None:
                return 0
            return submit_time

        # TODO: Add self.tags to tags everywhere
        mahler_query = dict(
            attributes=dict(experiment=query['experiment']),
            tags=[TRIAL_TAG] + self.tags
            )

        if 'status' in query:
            mahler_query['status'] = convert_orion_status(query['status'])

        trials = [
            TrialAdapter(t, objective=self.objective) for t in self.client.find(**mahler_query)
            if not self._should_ignore(t.tags)
        ]
        trials.sort(key=sort_key)
        return trials

    def retrieve_result(self, trial, *args, **kwargs):
        """Fetch the result from a given medium (file, db, socket, etc..) for a given trial and
        insert it into the trial object
        """
        if not isinstance(trial, TrialAdapter):
            task = self.client.find(attributes=dict(id=trial.id))[0]
            trial = TrialAdapter(task, trial, objective=self.objective)

        assert trial.objective is not None, 'Trial should have returned an objective value!'
        log.info("trial objective is (%s: %s)", self.objective, trial.objective.value)

        return trial

    def fetch_pending_trials(self, experiment):
        """See :func:`~orion.storage.BaseStorageProtocol.fetch_pending_trials`"""
        pending_status = ['OnHold', 'Queued', 'Suspended', 'Interrupted',
                          'FailedOver', 'SwitchedOver']

        query = dict(
            experiment=experiment.id,
            status=pending_status
        )

        return self._fetch_trials(query)

    def set_trial_status(self, trial, status, heartbeat=None):
        """Update the trial status and the heartbeat

        Raises
        ------
        FailedUpdate
            The exception is raised if the status of the trial object
            does not match the status in the database

        """
        if hearbeat is not None and trial.status == status == 'reserved':
            # NOTE: Mahler takes care of the heartbeat
            return trial

        if status == ["interrupted", "completed", "broken"]:
            # NOTE: Mahler takes care of this
            return trial

        if not isinstance(trial, TrialAdapter):
            task = self.client.find(attributes=dict(id=trial.id))[0]
            trial = TrialAdapter(task, trial, objective=self.objective)

        if new_status == "suspended":
            try:
                mahler_client.suspend(trial.task, 'from Oríon')
            except RaceCondition:
                raise FailedUpdate() from e
        else:
            raise ValueError(f'Unsupported status: {status}')

        trial.orion_trial.status = status
        return trial

    def fetch_trials(self, experiment=None, uid=None):
        """See :func:`~orion.storage.BaseStorageProtocol.fetch_trials`"""
        if uid and experiment:
            assert experiment.id == uid

        if uid is None:
            if experiment is None:
                raise MissingArguments('experiment or uid need to be defined')

            uid = experiment.id

        return self._fetch_trials(dict(experiment=uid))

    def get_trial(self, trial=None, uid=None):
        """See :func:`~orion.storage.BaseStorageProtocol.get_trials`"""
        if trial is not None and uid is not None:
            assert trial.id == uid

        if uid is None:
            if trial is None:
                raise MissingArguments('trial or uid argument should be populated')

            uid = trial.id

        mahler_query = dict(
            attributes=dict(_id=uid),
            tags=[TRIAL_TAG] + self.tags
            )

        trials = list(self.client.find(**mahler_query))

        if not trials:
            return None

        assert len(trials) == 1, len(trials)
        return TrialAdapter(trials[0], objective=self.objective)

    def reserve_trial(self, experiment):
        """Select a pending trial and reserve it for the worker"""
        # NOTE: This is handled by mahler workers.
        return None

    def fetch_lost_trials(self, experiment):
        """Fetch all trials that have a heartbeat older than
        some given time delta (2 minutes by default)
        """
        # NOTE: This is handled in Mahler.
        return []

    def push_trial_results(self, trial):
        """Push the trial's results to the database"""
        # Mahler already pushed the info no need to do it here
        pass

    def fetch_noncompleted_trials(self, experiment):
        """Fetch all non completed trials"""
        non_completed_status =['OnHold', 'Queued', 'Suspended', 'Interrupted',
                               'FailedOver', 'SwitchedOver', 'Running', 'Reserved',
                               'Cancelled', 'Acknowledged']
        query = dict(
            experiment=experiment.id,
            status=non_completed_status
        )
        return self._fetch_trials(query)

    def fetch_trial_by_status(self, experiment, status):
        """Fetch all trials with the given status"""
        trials = self._fetch_trials(dict(status=status, experiment=experiment.id))
        return trials

    def count_completed_trials(self, experiment):
        """Count the number of completed trials"""
        return len(self._fetch_trials(dict(status='Completed', experiment=experiment.id)))

    def count_broken_trials(self, experiment):
        """Count the number of broken trials"""
        return len(self._fetch_trials(dict(status='Broken', experiment=experiment.id)))

    def update_heartbeat(self, trial):
        """Update trial's heartbeat"""
        pass
