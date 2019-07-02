"""Module level configuration.

Blocks allows module-wide configuration values to be set using a YAML_
configuration file and `environment variables`_. Environment variables
override the configuration file which in its turn overrides the defaults.

The configuration is read from ``~/.blocksrc`` if it exists. A custom
configuration file can be used by setting the ``BLOCKS_CONFIG`` environment
variable. A configuration file is of the form:

.. code-block:: yaml

   data_path: /home/user/datasets

If a setting is not configured and does not provide a default, a
:class:`~.ConfigurationError` is raised when it is
accessed.

Configuration values can be accessed as attributes of
:const:`blocks.config.config`.

    >>> from blocks.config import config
    >>> print(config.default_seed) # doctest: +SKIP
    1

The following configurations are supported:

.. option:: default_seed

   The seed used when initializing random number generators (RNGs) such as
   NumPy :class:`~numpy.random.RandomState` objects as well as Theano's
   :class:`~theano.sandbox.rng_mrg.MRG_RandomStreams` objects. Must be an
   integer. By default this is set to 1.

.. option:: recursion_limit

   The recursion max depth limit used in
   :class:`~blocks.main_loop.MainLoop` as well as in other situations when
   deep recursion is required. The most notable example of such a situation
   is pickling or unpickling a complex structure with lots of objects, such
   as a big Theano computation graph.

.. option:: profile, BLOCKS_PROFILE

   A boolean value which determines whether to print profiling information
   at the end of a call to :meth:`.MainLoop.run`.

.. option:: log_backend

   The backend to use for logging experiments. Defaults to `python`, which
   stores the log as a Python object in memory. The other option is
   `sqlite`.

.. option:: sqlite_database, BLOCKS_SQLITEDB

   The SQLite database file to use.

.. option:: max_blob_size

   The maximum size of an object to store in an SQLite database in bytes.
   Objects beyond this size will trigger a warning. Defaults to 4 kilobyte.

.. option:: temp_dir

   The directory in which Blocks will create temporary files. If
   unspecified, the platform-dependent default chosen by the Python
   ``tempfile`` module is used.

.. _YAML: http://yaml.org/
.. _environment variables:
   https://en.wikipedia.org/wiki/Environment_variable

"""
import logging
import os

import yaml

from orion.core.utils.flatten import flatten


logger = logging.getLogger(__name__)


NOT_SET = object()


class Configuration(object):
    """Configuration object

    TODO
    """
    def __init__(self):
        """Initialization of an empty configuration object"""
        self.config = {}

    # def load_yaml(self):
    #     if 'BLOCKS_CONFIG' in os.environ:
    #         yaml_file = os.environ['BLOCKS_CONFIG']
    #     else:
    #         yaml_file = os.path.expanduser('~/.blocksrc')
    #     if os.path.isfile(yaml_file) and os.path.getsize(yaml_file):
    #         with open(yaml_file) as f:
    #             for key, value in yaml.safe_load(f).items():
    #                 if key not in self.config:
    #                     raise ValueError("Unrecognized config in YAML: {}"
    #                                      .format(key))
    #                 self.config[key]['yaml'] = value

    def __getattr__(self, key):
        """Get the value of the option

        Parameters
        ----------
        key: str
            Name of the option

        Returns
        -------
        None if the option does not exist, else value of the option.
        """
        if key == 'config':
            raise AttributeError
        if key not in self.config:
            return None

        config_setting = self.config[key]
        if 'value' in config_setting:
            value = config_setting['value']
        elif ('env_var' in config_setting and
              config_setting['env_var'] in os.environ):
            value = os.environ[config_setting['env_var']]
        elif 'yaml' in config_setting:
            value = config_setting['yaml']
        elif 'default' in config_setting:
            value = config_setting['default']
        else:
            return None

        return config_setting['type'](value)

    def __setattr__(self, key, value):
        """Set option value or subconfiguration

        Parameters
        ----------
        key: str
            The key or namespace to set the value of the configuration.
            If the configuration has subconfiguration, the key may be
            hierarchical with each levels seperated by dots.
            Ex: 'first.second.third'
        value: object or Configuration
            A general object to set an option or a configuration object to set
            a sub configuration.

        Raises
        ------
        TypeError
            If value is a configuration and an option is already defined for
            given key, or
            If the value has an invalid type for the given option, or
            If no option exists for the given key and the value is not a
            configuration object.
        """
        if key != 'config' and key in self.config:
            if isinstance(value, Configuration):
                raise TypeError("Cannot overwrite option {} with a configuration".format(key))

            try:
                self.config[key]['type'](value)
            except ValueError as e:
                raise TypeError("Option {} of type {} cannot be set to {} with type {}".format(
                                key, self.config[key]['type'], value, type(value))) from e

            self.config[key]['value'] = value

        elif key == 'config' or isinstance(value, Configuration):
            super(Configuration, self).__setattr__(key, value)

        else:
            raise TypeError("Can only set {} as a Configuration, not {}. Use add_option to set a new "
                            "option.".format(key, type(value)))

    def __setitem__(self, key, value):
        """Set option value using dict-like syntax

        Parameters
        ----------
        key: str
            The key or namespace to set the value of the configuration.
            If the configuration has subconfiguration, the key may be
            hierarchical with each levels seperated by dots.
            Ex: 'first.second.third'
        value: object
            A general object to set an option.
        """
        keys = key.split(".")
        # Recursively in sub configurations
        if len(keys) > 1:
            subconfig = getattr(self, keys[0]) 
            if subconfig is None:
                raise KeyError("'{}' is not defined in configuration.".format(keys[0]))
            subconfig[".".join(keys[1:])] = value
        # Set in current configuration
        else:
            setattr(self, keys[0], value)

    def __getitem__(self, key):
        """Get option value using dict-like syntax

        Parameters
        ----------
        key: str
            The key or namespace to set the value of the configuration.
            If the configuration has subconfiguration, the key may be
            hierarchical with each levels seperated by dots.
            Ex: 'first.second.third'
        """
        keys = key.split(".")
        # Recursively in sub configurations
        if len(keys) > 1:
            subconfig = getattr(self, keys[0]) 
            if subconfig is None:
                raise KeyError("'{}' is not defined in configuration.".format(keys[0]))
            return subconfig[".".join(keys[1:])]
        # Set in current configuration
        else:
            return getattr(self, keys[0])

    def add_option(self, key, type, default=NOT_SET, env_var=None):
        """Add a configuration setting.

        Parameters
        ----------
        key : str
            The name of the configuration setting. This must be a valid
            Python attribute name i.e. alphanumeric with underscores.
        type : function
            A function such as ``float``, ``int`` or ``str`` which takes
            the configuration value and returns an object of the correct
            type.  Note that the values retrieved from environment
            variables are always strings, while those retrieved from the
            YAML file might already be parsed. Hence, the function provided
            here must accept both types of input.
        default : object, optional
            The default configuration to return if not set. By default none
            is set and an error is raised instead.
        env_var : str, optional
            The environment variable name that holds this configuration
            value. If not given, this configuration can only be set in the
            YAML configuration file.

        """
        self.config[key] = {'type': type}
        if env_var is not None:
            self.config[key]['env_var'] = env_var
        if default is not NOT_SET:
            self.config[key]['default'] = default

    def to_dict(self):
        """Return a dictionary representing the configuration."""

        d = dict()
        for key in self.config.keys():
            d[key] = getattr(self, key)

        for name, attr in self.__dict__.items():
            if isinstance(attr, Configuration):
                d[name] = attr.to_dict()

        return d


def parse_config_file(configpath, config):
    try:
        with open(configpath) as f:
            cfg = yaml.safe_load(f)
            if cfg is None:
                return
            # implies that yaml must be in dict form
            for k, v in flatten(cfg).items():
                config[k] = v
    except IOError as e:  # default file could not be found
        logger.debug(e)
    except AttributeError as e:
        logger.warning("Problem parsing file: %s", configpath)
        logger.warning(e)
