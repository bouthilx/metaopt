# -*- coding: utf-8 -*-
"""
:mod:`orion.core.worker.consumer` -- Evaluate objective on a set of parameters
==============================================================================

.. module:: consumer
   :platform: Unix
   :synopsis: Call user's script as a black box process to evaluate a trial.

"""
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time

from orion.core.io.convert import JSONConverter
from orion.core.io.space_builder import SpaceBuilder
from orion.core.worker.trial import Trial

log = logging.getLogger(__name__)


def sigterm_handler(signal, frame):
    if sigterm_handler.triggered:
        return
    else:
        sigterm_handler.triggered = True

    raise Consumer.InterruptTrial("Experiment killed by externally from the system")


sigterm_handler.triggered = False


class Consumer(object):
    """Consume a trial by using it to initialize a black box to evaluate it.

    It uses an `Experiment` object to push an evaluated trial, if results are
    delivered to the worker process successfully.

    It forks another process which executes user's script with the suggested
    options. It expects results to be written in a **JSON** file, whose path
    has been defined in a special orion environmental variable which is set
    into the child process' environment.

    Attributes
    ----------
    experiment : `orion.core.worker.experiment.Experiment`
       Manager of current experiment
    space : `orion.algo.space.Space`
       Definition of problem's parameter space
    template_builder : `orion.core.io.space_builder.SpaceBuilder`
       Object that will build particular instances of the command line arguments
       and possibly configuration files, corresponding to a particular trial.
    script_path : str
       Path or name of the executable initializing user's code to be optimized
    tmp_dir : str
       Path to base temporary directory in user's system to output instances
       of configuration files, logs and comminucation files for a particular
       trial
    converter : `orion.core.io.converter.JSONConverter`
       Convenience object that parses and generates JSON files
    current_trial : `orion.core.worker.trial.Trial`
       If it is not None, then this is the trial which is being currently
       evaluated by the worker.

    """

    class InterruptTrial(Exception):
        """Raise this to communicate that `self.current_trial`'s evaluation
        has not been completed and that the execution of user's script has been
        interrupted.
        """

        pass

    class SuspendTrial(Exception):
        """Raise this to communicate that `self.current_trial`'s evaluation
        has not been completed and that the execution of user's script has been
        suspended.
        """

        pass

    def __init__(self, experiment):
        """Initialize a consumer.

        :param experiment: Manager of this experiment, provides convenient
           interface for interacting with the database.
        """
        log.debug("Creating Consumer object.")
        self.experiment = experiment
        self.space = experiment.space
        if self.space is None:
            raise RuntimeError("Experiment object provided to Consumer has not yet completed"
                               " initialization.")

        # Fetch space builder
        self.template_builder = SpaceBuilder()
        self.template_builder.build_from(experiment.metadata['user_args'])
        # Get path to user's script and infer trial configuration directory
        self.script_path = experiment.metadata['user_script']
        self.tmp_dir = os.path.join(tempfile.gettempdir(), 'orion')
        os.makedirs(self.tmp_dir, exist_ok=True)

        self.converter = JSONConverter()

        self.current_trial = None

    def consume(self, trial):
        """Execute user's script as a block box using the options contained
        within `trial`.

        This function is going to update a `trial` status as *'broken'*
        if user's script fatally crashed during execution, and as *'interrupted'*
        if a catchable terminating os signal was captured.

        It consists the main entry point to the functionality of this object.
        It will be called by `orion.core.worker.workon` to evaluate the
        performance of a particular `trial` on user's script.

        When a `trial` is successfully evaluated, its entry in the database
        is going to be updated with the results reported from user's code
        (described in ``self.current_trial.results``), and a ``'done'`` status.

        :type trial: `orion.core.worker.trial.Trial`

        .. note:: Out of the possible reasons that a user's script may crash,
           three case categories need to be taken into consideration:

           1. **There is a bug in user's code**: Conditional or not, due to a
              syntax or logical error, the process executing user's code will crash
              with a non-predictable code. A trial that was used will be saved in
              the database as *'broken'*.
           2. **Inputs defined by the trial cause an arithmetic NaN**: Ideally
              these errors should be handled by user's code, by catching existent
              NaN arising in their computations, and reporting a result with
              a use-case-specific undesired score. This will help some algorithms
              determine that trials that cause this behaviour are to be avoided.
              This is left as user's responsibility, because reporting an
              arbitrary score may not be compatible with the  use-case-specific
              definition of a trial's objective value and it also violates the
              desired separation of responsibility. If this case is left
              untreated, then (1) holds.
           3. **Inputs provided to user's code are invalid**: User's parsing
              code is expected to fail, if an incompatible set of intpus is given
              to the script. This fatal case should arise when the script's
              parameter space definition does not correspond to the version
              of the actual user's code to be executed. Treatment is left to
              the user. However **a fail fast solution could be detected**,
              because **argparse** (for instance) exits with code 2,
              when such a case happens. This argparse specific treatment
              is disputable. In favour of this handling is that this practice
              is traditional, but still, not enforced.

           .. seealso::

              Method `Consumer.interact_with_script`
                 Code which would exit with 2, if user's script exited with 2.

              `GNU bash manual, Exit Status <Exit Status>`_
                 Quoting: *... return an exit status of 2 to indicate
                 incorrect usage, generally invalid options or missing arguments.*

        .. _Exit Status:
           https://www.gnu.org/software/bash/manual/html_node/Exit-Status.html

        """
        returncode = None
        self.current_trial = trial
        try:
            signal.signal(signal.SIGTERM, sigterm_handler)
            returncode = self._consume()

        except Consumer.InterruptTrial:
            new_status = 'interrupted'
            raise

        except KeyboardInterrupt:
            new_status = 'suspended'
            time.sleep(0.1)
            print("\n\nTrial suspended. Press <ctrl-c> again to stop the experiment execution "
                  "altogether.\n\nReserving another trial in ")
            for i in range(5, 0, -1):
                print("{}...".format(i))
                time.sleep(1)
            print("\n\nNow continuing with other trials\n")

        except Consumer.SuspendTrial:
            new_status = 'suspended'

        except (SystemExit, Exception):
            new_status = 'broken'
            raise

        finally:
            if returncode == 0:
                log.debug("### Update successfully evaluated %s.", self.current_trial)
                self.experiment.push_completed_trial(self.current_trial)
            elif returncode is not None:
                self.experiment.push_completed_trial(self.current_trial, 'broken')
            else:
                self.experiment.push_completed_trial(self.current_trial, new_status)
            self.current_trial = None

    def _consume(self):
        log.debug("### Create new temporary directory at '%s':", self.tmp_dir)
        # XXX: wrap up with try/finally and safe-release resources explicitly
        # finally, substitute with statement with the creation of an object.
        trial_dir = os.path.join(self.tmp_dir, self.experiment.name, self.current_trial.id)
        if not os.path.isdir(trial_dir):
            os.makedirs(trial_dir)

        log.debug("## temp consumer context: %s", trial_dir)

        config_path = os.path.join(trial_dir, "trial.conf")
        with open(config_path, 'w') as f:
            f.write('')
            f.close()

        log.debug("## temp config file: %s", config_path)

        results_path = os.path.join(trial_dir, "results.out")
        with open(results_path, 'w') as f:
            f.write('')
            f.close()

        log.debug("## temp results file: %s", results_path)

        log.debug("## Building command line argument and configuration for trial.")
        cmd_args = self.template_builder.build_to(config_path,
                                                  self.current_trial,
                                                  self.experiment)

        command = cmd_args  # [self.script_path] + cmd_args
        return self.interact_with_script(command, results_path)

    def interact_with_script(self, command, results_path):
        """Interact with user's script by launching it in a separate process.

        When the process exits, evaluation information
        reported to a file will be attempted to be retrieved.

        It sets ``self.current_trial.results``, if possible.

        Override it with a subclass of `Consumer` to implement a different
        way of communication with user's code and possibly management of the
        child process.

        :returns: Exit code of the child process
        :rtype: int

        """
        log.debug("## Launch user's script as a subprocess and wait for finish.")
        script_process = self.launch_process(command, results_path)

        if script_process.returncode is not None:
            return script_process.returncode

        try:
            returncode = script_process.wait()
        except KeyboardInterrupt:
            script_process.terminate()
            raise

        try:
            if returncode != 0:
                log.error("Something went wrong. Check logs. Process "
                          "returned with code %d !", returncode)
                if returncode == 2:
                    # This is the exit code returned when invalid options are given,
                    # for example when argparse fails
                    sys.exit(2)
        finally:
            log.debug("## Parse results from file and fill corresponding Trial object.")
            try:
                results = self.converter.parse(results_path)
                self.current_trial.results = [Trial.Result(name=res['name'],
                                                           type=res['type'],
                                                           value=res['value']) for res in results]
            except ValueError:  # JSON error because file is empty
                pass

        return returncode

    @staticmethod
    def launch_process(command, results_path):
        """Facilitate launching a black-box trial.

        :returns: Child `subprocess.Popen` object

        """
        env = dict(os.environ)
        env['ORION_RESULTS_PATH'] = str(results_path)
        process = subprocess.Popen(command, env=env)
        returncode = process.poll()
        if returncode is not None and returncode < 0:
            log.error("Failed to execute script to evaluate trial. Process "
                      "returned with code %d !", returncode)

        return process
