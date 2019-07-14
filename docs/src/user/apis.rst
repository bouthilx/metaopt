****
APIs
****

Commandline API
===============


- *Experiment version control* to ensure coherence across executions
- *Decentralized optimization* to simplify parallelisation


.. code-block:: bash

   orion hunt --name exp-name ./script --lr~loguniform(1e-5,1)


Loop API
========

.. warning::

   Following documentation is a draft of the API planned for v0.2

The loop API will provide a simple interface to define an experiment using python code instead of
commandline interface and easily start off a worker and execute the optimization sequentially.
Note that this will also automatically synchronize with other process executing the same code,
making it easy to scale with parallel workers.

.. code-block:: python

   from orion.client import build_experiment

   experiment = build_experiment(
      name='exp-name',
      space={'lr': 'loguniform(1e-5, 1)', 'weight_decay': 'loguniform(1e-15, 1e-3)'},
      algorithm='random_search',
      target=my_function_to_optimize())

   experiment.execute()


Service API
===========

.. warning::

   Following documentation is a draft of the API planned for v0.2

The service API will provide a simple interface to request (pull) trials to execute and 
push back results. Note that this may be used with a centralized service or using the 
default decentralized backend of Oríon.

.. code-block:: python

   from orion.client import build_experiment

   experiment = build_experiment(
      name='exp-name',
      space={'lr': 'loguniform(1e-5, 1)', 'weight_decay': 'loguniform(1e-15, 1e-3)'},
      algorithm='random_search',
      service='decentralized')  # or some uri adress for centralized services

   trial = experiment.reserve_trial()

   result = whatever_is_going_on_in_the_middle(trial['params'], trial['working_dir'])

   experiment.report_results(trial, result)


Library API
===========

.. warning::

   The Library API is currently under construction and does not give access to all internals of
   Oríon yet. Better support for the Library API is planned for v0.2.

.. code-block:: python

   from orion.client import build_algorithm

   algorithm = build_algorithm(
      space={'lr': 'loguniform(1e-5, 1)', 'weight_decay': 'loguniform(1e-15, 1e-3)'},
      configurator='random_search')

   params = algorithm.suggest()

   result = whatever_is_going_on_in_the_middle(params)

   algorithm.observe(params, result)
