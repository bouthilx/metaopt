# -*- coding: utf-8 -*-
# pylint: disable=too-many-lines,arguments-differ
"""
:mod:`orion.core.evc.conflicts` -- Description and resolution of configuration conflicts
========================================================================================

.. module:: conflicts
   :platform: Unix
   :synopsis: Detection and description of configuration conflicts and their resolutions

Conflicts between a parent experiment and a child configuration exist in many different forms. This
module provides the function `detect_conflicts` to automatically detect them. Any conflict type
which inherits from class `Conflict` is used to detect corresponding conflicts. These conflicts
can than be used to generate resolutions which will generate adapters to make trials of
both experiments compatible when applicable.

Conflicts objects know how to resolve themselves but may lack information for doing so.  For
instance `ExperimentNameConflict` knows it may only be resolved with an `ExperimentNameResolution`,
but it cannot do so unless it is given a new name to instantiate the resolution.

Conflict objects may build different resolutions based on the input given. For instance
`MissingDimensionConflict` may instantiate a `RenameDimensionResolution` if a `NewDimensionConflict`
is passed to `try_resolve`, otherwise the resolution will be `RemoveDimensionResolution`.

In short, conflict knows:

#. How to detect themselves in pair old_config, new_config (`Conflict.detect()`)
#. How to resolve themselves (but may lack information for doing so) (`conflict.try_resolve()`)
#. How to build a diff for user interface (`conflict.diff`)
#. How to build a string to represent themselves in user interface (`repr(conflict)`)
#. How to find resolutions markers and their corresponding arguments
   (`conflict.get_marked_arguments`)

while resolution knows:

#. How to create adapters (`resolution.get_adapters()`)
#. How to find marked arguments for themselves (determining if this resolution was marked by user)
   (`resolution.find_marked_argument()`)
#. How to revert themselves and resetting the corresponding conflicts (`resolution.revert()`)
#. How to validate themselves on instantiation (`resolution.validate()`)
#. How to build a string to represent themselves in user interface (`repr(resolution)`)
   (note: this string is the one a user would use to mark the resolution in command line or in
   configuration file)

The class Conflicts is provided for convenience. It provides interface to register, fetch or
deprecate (remove) conflicts. Additionaly, it provides a helper method with wraps `try_resolve` of
the Conflict objects, handling invalid resolution errors or additional new conflicts
created by resolutions. For instance, a `RenameDimensionResolution` may create a new
`ChangedDimensionConflict` if the new name is associated to a different prior than the one
of the old name.
"""

from abc import (ABCMeta, abstractmethod)
import copy
import pprint
import traceback

import orion.core
from orion.algo.space import Dimension
from orion.core.evc import adapters
from orion.core.io.database import Database
from orion.core.io.space_builder import SpaceBuilder
from orion.core.utils.diff import colored_diff
from orion.core.utils.format_trials import standard_param_name


def _create_param(dimension, default_value):
    """Create a parameter dictionary based on dimension and default_value"""
    return dict(name=dimension.name, type=dimension.type, value=default_value)


def _build_extended_user_args(config):
    """Return a list of user arguments augmented with key-value pairs found in
    user's script's configuration file.
    """
    user_args = config['metadata']['user_args']

    space_builder = SpaceBuilder()
    space_builder.build_from(user_args)

    return user_args + [standard_param_name(key) + value
                        for key, value in space_builder.userconfig_expressions.items()]


def _build_space(config):
    """Build an optimization space based on given configuration"""
    space_builder = SpaceBuilder()
    space = space_builder.build_from(config['metadata']['user_args'])

    return space


def detect_conflicts(old_config, new_config):
    """Generate a Conflicts object with all conflicts found in pair (old_config, new_config)"""
    conflicts = Conflicts()
    for conflict_class in sorted(Conflict.__subclasses__(), key=lambda cls: cls.__name__):
        for conflict in conflict_class.detect(old_config, new_config):
            conflicts.register(conflict)

    return conflicts


class Conflicts(object):
    """Handler of a list of conflicts

    Registers, deprecate, resolve and fetch conflicts. Revert and fetch corresponding resolutions.

    The helper method `try_resolve` wraps `Conflict.try_resolve` objects,
    handling invalid resolution errors messages and adding to its list additional new conflicts
    created by resolutions.

    Attributes
    ----------
    conflicts: list of `Conflict`
        List of conflicts which may be resolved or not.

    """

    def __init__(self):
        """Initialize empty list of conflicts"""
        self.conflicts = []

    def register(self, conflict):
        """Add a new conflict to the list of conflicts"""
        self.conflicts.append(conflict)

    def revert(self, resolution_or_name):
        """Revert a resolution and deprecate conflicts if applicable"""
        name = str(resolution_or_name)
        resolution_strings = list(map(str, (c.resolution for c in self.get_resolved())))
        resolution = self.get_resolved()[resolution_strings.index(name)].resolution
        self.deprecate(resolution.revert())

    def _get(self, callback=None):
        """Fetch conflicts for which callback return True if callback is given, else return all"""
        return [conflict for conflict in self.conflicts
                if (callback is None or callback(conflict))]

    def get(self, types=(), dimension_name=None, callback=None):
        """Fetch conflicts

        Parameters
        ----------
        types: tuple of Conflict types
            List of conflict types to fetch
        dimension_name: None or string
            name of a dimension to fetch. If not None, will raise an error if no conflict found.
        callback: None or callable object
            If not None, only conflict for which the callback return True will be returned by
            get()

        Raises
        ------
        ValueError
            If argument dimension_name is not None and no conflict is found.

        """
        def wrap(types, dimension_name, callback):
            """Wrap types, dimension_name and callback inside another callback"""
            def _callback(conflict):
                if callback is not None and not callback(conflict):
                    return False
                if types and not isinstance(conflict, tuple(types)):
                    return False
                if (dimension_name is not None and
                        (not hasattr(conflict, "dimension") or
                         standard_param_name(conflict.dimension.name) != dimension_name)):
                    return False

                return True
            return _callback

        found_conflicts = self._get(wrap(types, dimension_name, callback))

        if dimension_name is not None and not found_conflicts:
            raise ValueError("Dimension name \'{}\' not found in conflicts".format(dimension_name))

        return found_conflicts

    def get_remaining(self, types=(), dimension_name=None, callback=None):
        """Fetch non resolved conflicts

        .. note::

            See :meth:`orion.core.evc.conflict.Conflicts.get` for more information.

        """
        def _is_not_resolved(conflict):
            return not conflict.is_resolved and (callback is None or callback(conflict))
        return self.get(types, dimension_name, callback=_is_not_resolved)

    def get_resolved(self, types=(), dimension_name=None, callback=None):
        """Fetch resolved conflicts

        .. note::

            See :meth:`orion.core.evc.conflict.Conflicts.get` for more information.

        """
        def _is_resolved(conflict):
            return conflict.is_resolved and (callback is None or callback(conflict))
        return self.get(types, dimension_name, callback=_is_resolved)

    def get_resolutions(self, types=(), dimension_name=None, callback=None):
        """Fetch resolutions

        Iterate over resolved conflicts and return their resolutions

        .. note::

            Some resolutions resolve many conflicts. This method only returns unique resolutions.

        .. note::

            See :meth:`orion.core.evc.conflict.Conflicts.get` for more information.

        """
        resolutions = set()
        for conflict in self.get_resolved(types, dimension_name, callback):
            if conflict.resolution not in resolutions:
                resolutions.add(conflict.resolution)
                yield conflict.resolution

    # API section
    @property
    def are_resolved(self):
        """Return True if all the current conflicts have been resolved"""
        return all(conflict.is_resolved for conflict in self.conflicts)

    def deprecate(self, conflicts):
        """Remove given conflicts from the internal list of conflicts"""
        for conflict in conflicts:
            self.conflicts.pop(self.conflicts.index(conflict))

    def try_resolve(self, conflict, *args, **kwargs):
        """Wrap call to conflict.try_resolve

        Catch errors on `conflict.try_resolve` and print traceback if argument `silence_errors` is
        False.

        Resolutions may generate side-effect conflicts. In such case, they are added to interval's
        list of conflicts.

        Parameter
        ---------
        conflict: `orion.ore.evc.conflicts.Conflict`
            Conflict object to call `try_resolve`.
        silence_errors: bool
            If True, errors raised on execution of conflict.try_resolve will be catched and
            silenced. If False, errors will be catched and traceback will be printed before
            methods return None. Defaults to False
        *args:
            Arguments to pass to `conflict.try_resolve`
        **kwargs:
            Keyword arguments to pass to `conflict.try_resolve`

        """
        silence_errors = kwargs.pop("silence_errors", False)
        try:
            resolution = conflict.try_resolve(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except Exception:  # pylint:disable=broad-except
            conflict.resolution = None
            conflict._is_resolved = None  # pylint:disable=protected-access
            if not silence_errors:
                print(traceback.format_exc())
            return None

        if resolution:
            self.conflicts += resolution.new_conflicts

        return resolution


class Conflict(object, metaclass=ABCMeta):
    """Representation of a conflict between two configurations

    This object is used to embody a conflict during a branching event and provides means to
    resolve itself and to represent itself in user interface.

    A conflict must provide implementations of:

    #. `detect()` -- How it is detected in a pair (old_config, new_config).
    #. `try_resolve()` -- How to resolve itself.
    #. `__repr__()` -- How to represent itself in user interface.

    Additionaly, it may also provide implementations of:

    #. `diff()` -- How to compute diff string.
    #. `get_marked_arguments()` -- How to find resolutions markers and their corresponding arguments
       in `new_config`.

    Attributes
    ----------
    old_config: dict
        Configuration of the parent experiment
    new_config: dict
        Configuration of the child experiment
    resolution: None or `orion.core.evc.conflicts.Resolution`
        None if not resolved or a `Resolution` object. Note that deprecated
        conflicts may be marked as resolved with `_is_resolved = True` even though
        `resolution` is `None`.

    """

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect all conflicts in given pair (old_config, new_config) and return a list of them"""
        pass

    def __init__(self, old_config, new_config):
        """Initialize conflict as non-resolved"""
        self.old_config = old_config
        self.new_config = new_config
        self._is_resolved = False
        self.resolution = None

    @property
    def is_resolved(self):
        """Return True if conflict is set as resolved or if it has a resolution"""
        return self._is_resolved or self.resolution is not None

    # pylint:disable=unused-argument,no-self-use
    def get_marked_arguments(self, conflicts):
        """Return arguments from marked resolutions in new configuration

        Some conflicts may be passed arguments with their marker to automate conflict resolution.
        For instance, a renaming resolution requires the name of a new dimension. In such case, the
        conflict for `missing_dim` would find `~missing_dim~>new_dim` in the user
        command line of configuration arguments, fetch `new_dim` from `conflicts` and return the
        dictionary of arguments `{'new_dimension_conflict': new_dimension_conflict}`.

        Parameters
        ----------
        conflicts: `orion.core.evc.conflicts.Conflicts`
            Handler of the list of conflicts.

        Returns
        -------
        dict
            Marked arguments for `conflict.try_resolve()`, which may latter be passed as
            `**kwargs`.

        """
        return {}

    @abstractmethod
    def try_resolve(self):
        """Try to create a resolution

        Conflict is then marked as resolved and its attribute `resolution` now points to the
        resolution.

        Returns
        -------
        None or `orion.core.evc.conflicts.Resolution`
            Returns None if the conflict is already resolved, otherwise
            it returns a resolution object if it is successful.

        Raises
        ------
        ValueError
            If the resolution cannot be created without arguments or if the arguments passed are not
            valid. This is specific to each child of `Conflict`

        """
        pass

    @property
    def diff(self):
        """Produce human-readable differences

        Returns
        -------
        None or str
            Returns None if the conflict cannot produce diffs, otherwise it returns
            the diff as a string (possibly multi-line).

        """
        return None

    # def get_hint(self):
    #     """Return a possible resolution as a string for user interface"""
    #     resolution = self.try_resolve()
    #     hint = "Try 'set {}'".format(str(resolution))
    #     resolution.revert()
    #     return hint

    @abstractmethod
    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        pass


class Resolution(object, metaclass=ABCMeta):
    """Representation of a resolution for a conflict between two configurations

    This object is used to embody a resolution of a conflict during a branching event and
    provides means to validate itself, produce side-effect conflicts, detect corresponding user
    markers, produce corresponding adapters and represent itself in user interface.

    The string representing a resolution is precisely what a user should type in command-line
    to resolve automatically a conflict.

    A resolution must provide implementations of:

    #. `get_adapters()` --  How to adapt trials from the two experiments.
    #. `__repr__()` -- How to represent itself in user interface. Note: this should correspond to
        what user should enter in command-line for automatic resolution.

    Additionaly, it may also provide implementations of:

    #. `revert()` -- How to revert the resolution and reset corresponding conflicts
    #. `_validate()` -- How to validate if arguments for the resolution are valid.
    #. `find_marked_argument()` -- How to find marked arguments in commandline call or script
        config

    Note that resolutions do not modify the configuration, with the exception of experiment name
    resolution, hence there is no support for diffs inside resolutions. The only diffs are between
    the two configurations, hence they are defined inside the conflicts.

    Attributes
    ----------
    conflict: `orion.core.evc.conflicts.Conflict`
        The conflict which is resolved by this resolution.
    new_conflicts: list of `orion.core.evc.conflicts.Conflict`
        The side-effect conflicts cause by this resolution.
    MARKER: None or string
        The special marker if resolution is intended for dimension conflicts, otherwise None
    ARGUMENT: None or string
        The command-line argument if the resolution is not intended for dimension conflicts.

    """

    MARKER = None
    ARGUMENT = None

    def __init__(self, conflict):
        """Initialize resolution and mark conflict as resolved"""
        self.conflict = conflict
        self.new_conflicts = []

        conflict.resolution = self

    def validate(self, *args, **kwargs):
        """Wrap validate method to revert resolution on invalid arguments"""
        try:
            self._validate(*args, **kwargs)
        except Exception:
            self.revert()
            raise

    def _validate(self, *args, **kwargs):
        """Validate arguments and raise a ValueError if they are invalid"""
        pass

    @classmethod
    def namespace(cls):
        """Return namespace corresponding to self.ARGUMENT

        ARGUMENT is a command line argument, thus something in the style of `--code-change-type`.
        When arguments are passed they are saved in namespace in the style of `code_change_type`.
        This property converts command-line style to namespace style.
        """
        if not cls.ARGUMENT:
            return None

        return cls.ARGUMENT.lstrip("-").replace("-", "_")

    def revert(self):
        """Reset conflict as well as side-effect conflicts and return the latter for deprecation"""
        self.conflict.resolution = None
        return []

    @abstractmethod
    def get_adapters(self):
        """Return adapters corresponding to the resolution"""
        pass

    @abstractmethod
    def __repr__(self):
        """Representation of the resolution as it should be provided in command line of
        configuration file by the user
        """
        pass

    def find_marked_argument(self):
        """Find commandline argument on configuration argument which marks this
        type of resolution for automatic resolution
        """
        new_config = self.conflict.new_config
        marked_argument = None
        if self.MARKER:
            for arg in _build_extended_user_args(new_config):
                if arg.lstrip("-").startswith(self.prefix):
                    marked_argument = arg
                    break
        else:
            marked_argument = orion.core.config.branching[self.namespace()]

        return marked_argument

    @property
    def is_marked(self):
        """If this resolution is specifically marked in commandline arguments or configuration
        arguments
        """
        return self.find_marked_argument() not in [None, False]


class NewDimensionConflict(Conflict):
    """Representation of a new dimension conflict

    Attributes
    ----------
    dimension: `orion.algo.space.Dimension`
        Dimension object which is defined in new_config but not in old_config.
    prior: string
        String representing the prior of the dimension

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect all new dimensions in `new_config` based on `old_config`"""
        old_space = _build_space(old_config)
        new_space = _build_space(new_config)
        for name, dim in new_space.items():
            new_prior = dim.get_prior_string()
            if name not in old_space:
                yield cls(old_config, new_config, dim, new_prior)

    def __init__(self, old_config, new_config, dimension, prior):
        """Initialize conflict as non-resolved"""
        super(NewDimensionConflict, self).__init__(old_config, new_config)
        self.dimension = dimension
        self.prior = prior

    def try_resolve(self, default_value=Dimension.NO_DEFAULT_VALUE):
        """Try to create a resolution AddDimensionResolution

        Parameters
        ----------
        default_value: object
            Default value for the new dimension. Defaults to `Dimension.NO_DEFAULT_VALUE`.

        Raises
        ------
        ValueError
            If default_value is invalid for the corresponding dimension.

        """
        if self.is_resolved:
            return None

        return self.AddDimensionResolution(self, default_value)

    @property
    def diff(self):
        """Produce human-readable differences"""
        return colored_diff("", self.dimension.get_string())

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        return "New {0}".format(standard_param_name(self.dimension.name))

    class AddDimensionResolution(Resolution):
        """Representation of a new dimension resolution

        Attributes
        ----------
        default_value: object
            Default value for the new dimension.

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        MARKER = "~+"

        def __init__(self, conflict, default_value=Dimension.NO_DEFAULT_VALUE):
            """Initialize resolution and mark conflict as resolved

            Parameters
            ----------
            conflict: `orion.core.evc.conflicts.Conflict`
                The conflict which is resolved by this resolution.
            default_value: object
                Default value for the new dimension. Defaults to `Dimension.NO_DEFAULT_VALUE`.
                If `Dimension.NO_DEFAULT_VALUE`, default_value from corresponding dimension will be
                used.

            Raises
            ------
            ValueError
                If default_value is invalid for the corresponding dimension.

            """
            super(NewDimensionConflict.AddDimensionResolution, self).__init__(conflict)
            if default_value is Dimension.NO_DEFAULT_VALUE:
                default_value = conflict.dimension.default_value
            else:
                default_value = conflict.dimension.cast(default_value)

            self.validate(default_value)

            self.default_value = default_value

        def _validate(self, default_value):
            """Validate default value is NO_DEFAULT_VALUE or is in dimension's interval"""
            if ((default_value is not Dimension.NO_DEFAULT_VALUE) and
                    (default_value not in self.conflict.dimension)):
                raise ValueError(
                    "Default value `{}` is outside of dimension's prior interval `{}`".format(
                        default_value, self.conflict.prior))

        def get_adapters(self):
            """Return DimensionAddition adapter"""
            default_param = _create_param(self.conflict.dimension, self.default_value)
            return [adapters.DimensionAddition(default_param)]

        @property
        def prefix(self):
            """Build the prefix including the marker"""
            return '{0}{1}'.format(standard_param_name(self.conflict.dimension.name),
                                   self.MARKER)

        @property
        def new_prior(self):
            """Build the new prior string, including the default value"""
            tmp_dim = copy.deepcopy(self.conflict.dimension)
            tmp_dim._default_value = self.default_value  # pylint:disable=protected-access
            return tmp_dim.get_prior_string()

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return '{0}{1}'.format(self.prefix, self.new_prior)


class ChangedDimensionConflict(Conflict):
    """Representation of a changed prior conflict

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect all changed dimensions in `new_config` based on `old_config`"""
        old_space = _build_space(old_config)
        new_space = _build_space(new_config)
        for name, dim in new_space.items():
            if name not in old_space:
                continue

            new_prior = dim.get_prior_string()
            old_prior = old_space[name].get_prior_string()

            if new_prior != old_prior:
                yield cls(old_config, new_config, dim, old_prior, new_prior)

    def __init__(self, old_config, new_config, dimension, old_prior, new_prior):
        """Initialize conflict as non-resolved"""
        super(ChangedDimensionConflict, self).__init__(old_config, new_config)
        self.dimension = dimension
        self.old_prior = old_prior
        self.new_prior = new_prior

    def try_resolve(self):
        """Try to create a resolution ChangeDimensionResolution"""
        if self.is_resolved:
            return None

        return self.ChangeDimensionResolution(self)

    @property
    def diff(self):
        """Produce human-readable differences"""
        return colored_diff(self.old_prior, self.new_prior)

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        return "{0}~{1} != {0}~{2}".format(standard_param_name(self.dimension.name),
                                           self.old_prior, self.new_prior)

    class ChangeDimensionResolution(Resolution):
        """Representation of a changed prior resolution

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        MARKER = "~+"

        def get_adapters(self):
            """Return DimensionPriorChange adapter"""
            return [adapters.DimensionPriorChange(
                self.conflict.dimension.name, self.conflict.old_prior, self.conflict.new_prior)]

        @property
        def prefix(self):
            """Build the new prior string, including the default value"""
            return '{0}{1}'.format(standard_param_name(self.conflict.dimension.name),
                                   self.MARKER)

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return "{0}{1}".format(self.prefix, self.conflict.new_prior)


class MissingDimensionConflict(Conflict):
    """Representation of a new dimension conflict

    Attributes
    ----------
    dimension: `orion.algo.space.Dimension`
        Dimension object which is defined in new_config but not in old_config.
    prior: string
        String representing the prior of the dimension

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect all missing dimensions in `new_config` based on `old_config`"""
        for conflict in NewDimensionConflict.detect(new_config, old_config):
            yield cls(old_config, new_config, conflict.dimension, conflict.prior)

    def __init__(self, old_config, new_config, dimension, prior):
        """Initialize conflict as non-resolved"""
        super(MissingDimensionConflict, self).__init__(old_config, new_config)
        self.dimension = dimension
        self.prior = prior

    def get_marked_arguments(self, conflicts):
        """Find and return marked arguments for remove or rename resolution

        .. seealso::

            :meth:`orion.core.evc.conflicts.Conflict.get_marked_arguments`

        """
        marked_remove_arguments = self.get_marked_remove_arguments(conflicts)

        if marked_remove_arguments:
            return marked_remove_arguments

        return self.get_marked_rename_arguments(conflicts)

    # pylint:disable=unused-argument
    def get_marked_remove_arguments(self, conflicts):
        """Find and return marked arguments for remove resolution

        .. seealso::

            :meth:`orion.core.evc.conflicts.Conflict.get_marked_arguments`

        """
        if self.is_resolved:
            return {}

        remove_dimension_resolution = copy.deepcopy(self).try_resolve()

        if not remove_dimension_resolution:
            return {}

        arguments = remove_dimension_resolution.find_marked_argument()
        if arguments:
            new_default_value = arguments.split(
                MissingDimensionConflict.RemoveDimensionResolution.MARKER)[1]

            if not new_default_value:
                new_default_value = Dimension.NO_DEFAULT_VALUE
            else:
                new_default_value = self.dimension.cast(new_default_value)

            return {'default_value': new_default_value}
        return {}

    def get_marked_rename_arguments(self, conflicts):
        """Find and return marked arguments for rename resolution

        .. seealso::

            :meth:`orion.core.evc.conflicts.Conflict.get_marked_arguments`

        """
        new_dimension_conflicts = conflicts.get([NewDimensionConflict])
        if not new_dimension_conflicts:
            return {}

        resolution = copy.deepcopy(self).try_resolve(
            new_dimension_conflict=copy.deepcopy(new_dimension_conflicts[0]))

        if not resolution:
            return {}

        arguments = resolution.find_marked_argument()

        if arguments:
            new_dimension_name = "~>".join(arguments.split('~>')[1:])

            try:
                conflict = conflicts.get([NewDimensionConflict],
                                         dimension_name=new_dimension_name)[0]
            except ValueError as e:
                if "Dimension name '{}' not found".format(new_dimension_name) not in str(e):
                    return {}

                raise

            if conflict.is_resolved:
                conflicts.revert(str(conflict.resolution))

            return {'new_dimension_conflict': conflict}

        return {}

    def try_resolve(self, new_dimension_conflict=None, default_value=Dimension.NO_DEFAULT_VALUE):
        """Try to create a resolution RenameDimensionResolution of RemoveDimensionResolution

        Parameter
        ---------
        new_dimension_conflict: None or `orion.core.evc.conflicts.NewDimensionConflict`
            Dimension used for a rename resolution. If None, a remove resolution will be created
            instead.
        default_value: object
            Default value for the missing dimension. Defaults to `Dimension.NO_DEFAULT_VALUE`.
            If `Dimension.NO_DEFAULT_VALUE`, default_value from corresponding dimension will be
            used. This argument is ignored if new_dimension_conflict is not None.

        Raises
        ------
        ValueError
            If default_value is invalid for the corresponding dimension.

        """
        if self.is_resolved:
            return None

        if new_dimension_conflict:
            return MissingDimensionConflict.RenameDimensionResolution(self, new_dimension_conflict)

        return MissingDimensionConflict.RemoveDimensionResolution(self, default_value)

    @property
    def diff(self):
        """Produce human-readable differences"""
        return colored_diff(self.dimension.get_string(), "")

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        return "Missing {0}".format(standard_param_name(self.dimension.name))

    class RenameDimensionResolution(Resolution):
        """Representation of a rename dimension resolution

        Attributes
        ----------
        new_dimension_conflict: `orion.core.evc.conflicts.NewDimensionConflict`
            New dimension to rename to.

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        MARKER = "~>"

        def __init__(self, conflict, new_dimension_conflict):
            """Initialize resolution and mark conflict as resolved

            .. note::

                Will create a side-effect conflict if the new dimension have a different prior than
                the old dimension.

            Parameters
            ----------
            new_dimension_conflict: `orion.core.evc.conflicts.NewDimensionConflict`
                Dimension used for a rename resolution.

            """
            super(MissingDimensionConflict.RenameDimensionResolution, self).__init__(conflict)

            self.new_dimension_conflict = new_dimension_conflict
            new_dimension_conflict.resolution = self

            if self.conflict.prior != new_dimension_conflict.prior:
                changed_dimension_conflict = ChangedDimensionConflict(
                    self.conflict.old_config,
                    self.conflict.new_config,
                    new_dimension_conflict.dimension, self.conflict.prior,
                    new_dimension_conflict.prior)

                self.new_conflicts.append(changed_dimension_conflict)

        def revert(self):
            """Reset conflict as well as side-effect conflicts and return the latter for
            deprecation
            """
            self.conflict.resolution = None
            self.new_dimension_conflict.resolution = None

            deprecated_conflicts = self.new_conflicts
            if deprecated_conflicts:
                deprecated_conflicts[0]._is_resolved = True  # pylint:disable=protected-access

            self.new_conflicts = []

            return deprecated_conflicts

        def get_adapters(self):
            """Return DimensionRenaming adapter"""
            return [adapters.DimensionRenaming(
                self.conflict.dimension.name, self.new_dimension_conflict.dimension.name)]

        @property
        def prefix(self):
            """Build the new prior string, including the default value"""
            return "{0}{1}".format(
                standard_param_name(self.conflict.dimension.name),
                self.MARKER)

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return "{0}{1}".format(
                self.prefix, standard_param_name(self.new_dimension_conflict.dimension.name))

    class RemoveDimensionResolution(Resolution):
        """Representation of a remove dimension resolution

        Attributes
        ----------
        default_value: object
            Default value for the missing dimension.

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        MARKER = "~-"

        def __init__(self, conflict, default_value=Dimension.NO_DEFAULT_VALUE):
            """Initialize resolution and mark conflict as resolved

            Parameters
            ----------
            conflict: `orion.core.evc.conflicts.Conflict`
                The conflict which is resolved by this resolution.
            default_value: object
                Default value for the missing dimension. Defaults to `Dimension.NO_DEFAULT_VALUE`.
                If `Dimension.NO_DEFAULT_VALUE`, default_value from corresponding dimension will be
                used.

            Raises
            ------
            ValueError
                If default_value is invalid for the corresponding dimension.

            """
            super(MissingDimensionConflict.RemoveDimensionResolution, self).__init__(conflict)
            if default_value is Dimension.NO_DEFAULT_VALUE:
                default_value = conflict.dimension.default_value
            else:
                default_value = self.conflict.dimension.cast(default_value)

            self.validate(default_value)
            self.default_value = default_value

        def _validate(self, default_value):
            """Validate default value is NO_DEFAULT_VALUE or is in dimension's interval"""
            if ((default_value is not Dimension.NO_DEFAULT_VALUE) and
                    (default_value not in self.conflict.dimension)):
                raise ValueError(
                    "Default value `{}` is outside of dimension's prior interval `{}`".format(
                        default_value, self.conflict.prior))

        def get_adapters(self):
            """Return DimensionDeletion adapter"""
            param = _create_param(self.conflict.dimension, self.default_value)
            return [adapters.DimensionDeletion(param)]

        @property
        def prefix(self):
            """Build the new prior string, including the default value"""
            return '{0}{1}'.format(standard_param_name(self.conflict.dimension.name),
                                   self.MARKER)

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            string = self.prefix
            if self.default_value is not Dimension.NO_DEFAULT_VALUE:
                string += "{}".format(repr(self.default_value))
            return string


class AlgorithmConflict(Conflict):
    """Representation of an algorithm configuration conflict

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect if algorithm definition in `new_config` differs from `old_config`"""
        if old_config['algorithms'] != new_config['algorithms']:
            yield cls(old_config, new_config)

    def try_resolve(self):
        """Try to create a resolution AlgorithmResolution"""
        if self.is_resolved:
            return None

        return self.AlgorithmResolution(self)

    @property
    def diff(self):
        """Produce human-readable differences"""
        return colored_diff(
            pprint.pformat(self.old_config['algorithms']),
            pprint.pformat(self.new_config['algorithms']))

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        # TODO: select different subset rather than printing the old dict
        return "{0}\n   !=\n{1}".format(
            pprint.pformat(self.old_config['algorithms']),
            pprint.pformat(self.new_config['algorithms']))

    class AlgorithmResolution(Resolution):
        """Representation of an algorithn configuration resolution

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        ARGUMENT = "--algorithm-change"

        def get_adapters(self):
            """Return AlgorithmChange adapter"""
            return [adapters.AlgorithmChange()]

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return "{0}".format(self.ARGUMENT)


class CodeConflict(Conflict):
    """Representation of code change conflict

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect if commit hash in `new_config` differs from `old_config`"""
        old_hash_commit = old_config['metadata'].get('hash_commit')
        new_hash_commit = new_config['metadata'].get('hash_commit')
        if old_hash_commit != new_hash_commit:
            yield cls(old_config, new_config)

    def get_marked_arguments(self, conflicts):
        """Find and return marked arguments for code change conflict

        .. seealso::

            :meth:`orion.core.evc.conflicts.Conflict.get_marked_arguments`

        """
        change_type = orion.core.config.branching[self.CodeResolution.namespace()]

        if change_type:
            return dict(change_type=change_type)

        return {}

    def try_resolve(self, change_type=None):
        """Try to create a resolution CodeResolution

        Parameter
        ---------
        change_type: None or string
            One of the types defined in `orion.core.evc.adapters.CodeChange.types`.

        Raises
        ------
        ValueError
            If change_type is not in `orion.core.evc.adapters.CodeChange.types`.

        """
        if self.is_resolved:
            return None

        return self.CodeResolution(self, change_type)

    @property
    def diff(self):
        """Produce human-readable differences"""
        return colored_diff(
            self.old_config['metadata'].get('hash_commit'),
            self.new_config['metadata'].get('hash_commit'))

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        return "Old hash commit \'{0}\' != new hash commit \'{1}\'".format(
            self.old_config['metadata'].get('hash_commit'),
            self.new_config['metadata'].get('hash_commit'))

    class CodeResolution(Resolution):
        """Representation of an code change resolution

        Attributes
        ----------
        conflict: `orion.core.evc.conflicts.Conflict`
            The conflict which is resolved by this resolution.
        change_type: string
            One of the types defined in `orion.core.evc.adapters.CodeChange.types`.

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        ARGUMENT = "--code-change-type"

        def __init__(self, conflict, change_type):
            """Initialize resolution and mark conflict as resolved

            Parameters
            ----------
            conflict: `orion.core.evc.conflicts.Conflict`
                The conflict which is resolved by this resolution.
            change_type: string
                One of the types defined in `orion.core.evc.adapters.CodeChange.types`.

            Raises
            ------
            ValueError
                If change_type is not in `orion.core.evc.adapters.CodeChange.types`.

            """
            super(CodeConflict.CodeResolution, self).__init__(conflict)

            self.validate(change_type)
            self.type = change_type

        def _validate(self, change_type):
            """Validate change_type is in `orion.core.evc.adapters.CodeChange.types`"""
            adapters.CodeChange.validate(change_type)

        def get_adapters(self):
            """Return CodeChange adapter"""
            return [adapters.CodeChange(self.type)]

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return "{0} {1}".format(self.ARGUMENT, self.type)


class CommandLineConflict(Conflict):
    """Representation of commandline change conflict

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def get_nameless_args(cls, config):
        """Get user's commandline arguments which are not dimension definitions"""
        space_builder = SpaceBuilder()
        space_builder.build_from(config['metadata']['user_args'])
        nameless_args = dict((key, value)
                             for (key, value) in space_builder.userargs_tmpl.items()
                             if key.startswith('_'))

        return " ".join(arg for key, arg in sorted(nameless_args.items(), key=lambda a: a[0]))

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect if command line call in `new_config` differs from `old_config`"""
        old_nameless_args = cls.get_nameless_args(old_config)
        new_nameless_args = cls.get_nameless_args(new_config)

        if old_nameless_args != new_nameless_args:
            yield cls(old_config, new_config)

    def get_marked_arguments(self, conflicts):
        """Find and return marked arguments for cli change conflict

        .. seealso::

            :meth:`orion.core.evc.conflicts.Conflict.get_marked_arguments`

        """
        change_type = orion.core.config.branching[self.CommandLineResolution.namespace()]

        if change_type:
            return dict(change_type=change_type)

        return {}

    def try_resolve(self, change_type=None):
        """Try to create a resolution CommandLineResolution

        Parameter
        ---------
        change_type: None or string
            One of the types defined in `orion.core.evc.adapters.CommandLineChange.types`.

        Raises
        ------
        ValueError
            If change_type is not in `orion.core.evc.adapters.CommandLineChange.types`.

        """
        if self.is_resolved:
            return None

        return self.CommandLineResolution(self, change_type)

    @property
    def diff(self):
        """Produce human-readable differences"""
        return colored_diff(self.get_nameless_args(self.old_config),
                            self.get_nameless_args(self.new_config))

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        return "Old arguments \'{0}\' != new arguments \'{1}\'".format(
            self.get_nameless_args(self.old_config),
            self.get_nameless_args(self.new_config))

    class CommandLineResolution(Resolution):
        """Representation of an commandline change resolution

        Attributes
        ----------
        conflict: `orion.core.evc.conflicts.Conflict`
            The conflict which is resolved by this resolution.
        change_type: string
            One of the types defined in `orion.core.evc.adapters.CommandLineChange.types`.

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        ARGUMENT = "--cli-change-type"

        def __init__(self, conflict, change_type):
            """Initialize resolution and mark conflict as resolved

            Parameters
            ----------
            conflict: `orion.core.evc.conflicts.Conflict`
                The conflict which is resolved by this resolution.
            change_type: string
                One of the types defined in `orion.core.evc.adapters.CommandLineChange.types`.

            Raises
            ------
            ValueError
                If change_type is not in `orion.core.evc.adapters.CommandLineChange.types`.

            """
            super(CommandLineConflict.CommandLineResolution, self).__init__(conflict)

            self.validate(change_type)
            self.type = change_type

        def _validate(self, change_type):
            """Validate change_type is in `orion.core.evc.adapters.CommandLineChange.types`"""
            adapters.CommandLineChange.validate(change_type)

        def get_adapters(self):
            """Return CommandLineChange adapter"""
            return [adapters.CommandLineChange(self.type)]

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return "{0} {1}".format(self.ARGUMENT, self.type)


class ScriptConfigConflict(Conflict):
    """Representation of script configuration change conflict

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def get_nameless_config(cls, config):
        """Get configuration dict of user's script without dimension definitions"""
        space_builder = SpaceBuilder()
        space_builder.build_from(config['metadata']['user_args'])
        nameless_config = dict((key, value)
                               for (key, value) in space_builder.userconfig_nameless.items())

        return nameless_config

    @classmethod
    def detect(cls, old_config, new_config):
        """Detect if user's script's config file in `new_config` differs from `old_config`"""
        old_script_config = cls.get_nameless_config(old_config)
        new_script_config = cls.get_nameless_config(new_config)

        if old_script_config != new_script_config:
            yield cls(old_config, new_config)

    def get_marked_arguments(self, conflicts):
        """Find and return marked arguments for user's script's config change conflict

        .. seealso::

            :meth:`orion.core.evc.conflicts.Conflict.get_marked_arguments`

        """
        change_type = orion.core.config.branching[self.ScriptConfigResolution.namespace()]

        if change_type:
            return dict(change_type=change_type)

        return {}

    def try_resolve(self, change_type=None):
        """Try to create a resolution ScriptConfigResolution

        Parameter
        ---------
        change_type: None or string
            One of the types defined in `orion.core.evc.adapters.ScriptConfigChange.types`.

        Raises
        ------
        ValueError
            If change_type is not in `orion.core.evc.adapters.ScriptConfigChange.types`.

        """
        if self.is_resolved:
            return None

        return self.ScriptConfigResolution(self, change_type)

    @property
    def diff(self):
        """Produce human-readable differences"""
        return colored_diff(
            pprint.pformat(self.get_nameless_config(self.old_config)),
            pprint.pformat(self.get_nameless_config(self.new_config)))

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        return "Script's configuration file changed"

    class ScriptConfigResolution(Resolution):
        """Representation of a script configuration change resolution

        Attributes
        ----------
        conflict: `orion.core.evc.conflicts.Conflict`
            The conflict which is resolved by this resolution.
        change_type: string
            One of the types defined in `orion.core.evc.adapters.ScriptConfighange.types`.

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        """

        ARGUMENT = "--config-change-type"

        def __init__(self, conflict, change_type):
            """Initialize resolution and mark conflict as resolved

            Parameters
            ----------
            conflict: `orion.core.evc.conflicts.Conflict`
                The conflict which is resolved by this resolution.
            change_type: string
                One of the types defined in `orion.core.evc.adapters.ScriptConfigChange.types`.

            Raises
            ------
            ValueError
                If change_type is not in `orion.core.evc.adapters.ScriptConfigChange.types`.

            """
            super(ScriptConfigConflict.ScriptConfigResolution, self).__init__(conflict)

            self.validate(change_type)
            self.type = change_type

        def _validate(self, change_type):
            """Validate change_type is in `orion.core.evc.adapters.ScriptConfigChange.types`"""
            adapters.ScriptConfigChange.validate(change_type)

        def get_adapters(self):
            """Return ScriptdConfigChange adapter"""
            return [adapters.ScriptConfigChange(self.type)]

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return "{0} {1}".format(self.ARGUMENT, self.type)


class ExperimentNameConflict(Conflict):
    """Representation of experiment name conflict

    .. seealso ::

        :class:`orion.core.evc.conflicts.Conflict`

    """

    @classmethod
    def detect(cls, old_config, new_config):
        """Return experiment name conflict no matter what

        Branching event cannot be triggered experiment name is not the same.
        """
        yield cls(old_config, new_config)

    def get_marked_arguments(self, conflicts):
        """Find and return marked arguments for experiment name conflict

        .. seealso::

            :meth:`orion.core.evc.conflicts.Conflict.get_marked_arguments`

        """
        new_name = orion.core.config.branching[self.ExperimentNameResolution.namespace()]

        if new_name:
            return dict(new_name=new_name)

        return {}

    @property
    def username(self):
        """Retrieve username for configuration"""
        return self.new_config['metadata']['user']

    def try_resolve(self, new_name=None):
        """Try to create a resolution ExperimentNameResolution

        Parameter
        ---------
        new_name: None or string
            A new name for the branching experiment. A ValueError is raised if name is already in
            database.

        Raises
        ------
        ValueError
            If name already exists in database for current user.

        """
        if self.is_resolved:
            return None

        return self.ExperimentNameResolution(self, new_name)

    @property
    def diff(self):
        """Produce *no* diff"""
        return None

    def __repr__(self):
        """Reprensentation of the conflict for user interface"""
        return "Experiment name \'{0}\' already exist for user \'{1}\'".format(
            self.old_config['name'], self.username)

    class ExperimentNameResolution(Resolution):
        """Representation of an experiment name resolution

        .. seealso ::

            :class:`orion.core.evc.conflicts.Resolution`

        Attributes
        ----------
        conflict: `orion.core.evc.conflicts.Conflict`
            The conflict which is resolved by this resolution.
        new_name: string
            A new name for the branching experiment.

        """

        ARGUMENT = "--branch"

        def __init__(self, conflict, new_name):
            """Initialize resolution and mark conflict as resolved

            Parameters
            ----------
            conflict: `orion.core.evc.conflicts.Conflict`
                The conflict which is resolved by this resolution.
            new_name: string
                A new name for the branching experiment. A ValueError is raised if name is already
                in database.

            Raises
            ------
            ValueError
                If name already exists in database for current user.

            """
            super(ExperimentNameConflict.ExperimentNameResolution, self).__init__(conflict)

            self.new_name = new_name
            self.old_name = self.conflict.new_config['name']
            self.validate(new_name)
            self.conflict.new_config['name'] = new_name

        def _validate(self, new_name):
            """Validate new_name is not in database for current user"""
            if new_name is None:
                raise ValueError("No new name provided. Cannot resolve experiment name conflict.")

            # TODO: WARNING!!! _name_is_unique could lead to race conditions,
            # The resolution may become invalid before the branching experiment is
            # registered. What should we do in such case?
            if not self._name_is_unique(new_name):
                raise ValueError(
                    "Experiment name \'{0}\' already exist for user \'{1}\'".format(
                        new_name, self.conflict.username))

        def _name_is_unique(self, name):
            """Return True if given name is not in database for current user"""
            query = {'name': name, 'metadata.user': self.conflict.username}

            named_experiments = Database().count('experiments', query)
            return named_experiments == 0

        def revert(self):
            """Reset conflict set experiment name back to old one in new configuration"""
            self.conflict.new_config['name'] = self.old_name
            return super(ExperimentNameConflict.ExperimentNameResolution, self).revert()

        def get_adapters(self):
            """Return no adapters, trials need to adaptation to new experiment name"""
            return []

        def __repr__(self):
            """Representation of the resolution as it should be provided in command line of
            configuration file by the user
            """
            return "{0} {1}".format(self.ARGUMENT, self.new_name)
