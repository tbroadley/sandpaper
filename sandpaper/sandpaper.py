#!/usr/bin/env python
# -*- encoding: utf-8 -*-
#
# Copyright (c) 2017 Stephen Bunn (stephen@bunn.io)
# MIT License <https://opensource.org/licenses/MIT>

import os
import hashlib
import datetime
import functools
import collections

import six
import regex
import arrow
import pyexcel


def value_rule(func):
    """ A meta wrapper for value normalization rules.

    .. note:: Value rules take in a full record and a column name as
        implicit parameters. They are expected to return the value at
        ``record[column]`` that has be normalized by the rule.

    :param callable func: The normalization rule
    :returns: The wrapped normalization rule
    :rtype: callable
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self.value_rules.add(func)
        self.rules.append((func, args, kwargs,))
        return self

    return wrapper


def record_rule(func):
    """ A meta wrapper for table normalization rules.

    .. note:: Record rules are applied after all value rules have been applied
        to a record. They take in a full record as an implicit parameter and
        are expected to return the normalized record back.

    :param callable func: The normalization rule
    :returns: The wrapped normalization rule
    :rtype: callable
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self.record_rules.add(func)
        self.rules.append((func, args, kwargs,))
        return self

    return wrapper


class SandPaper(object):
    """ The SandPaper object.

    Allows chained data normalization across multiple different table type
        data files such as ``.csv``, ``.xls``, and ``.xlsx``.
    """

    __available_filters = (
        'column_filter', 'value_filter', 'callable_filter',
    )
    __default_apply = {
        'auto_detect_datetime': False,
    }

    def __init__(self, name=None):
        """ Initializes the SandPaper object.

        .. note:: If a descriptive name is not provided, the name references a
            continually updating uid hash of the active rules.

        :param str name: The descriptive name of the SandPaper object
        """

        # TODO: find a way to provide better handling for data offsets
        if name is not None:
            self.name = name

    def __repr__(self):
        """ Returns a string representation of a SandPaper instance.

        :returns: A string representation of a SandPaper instance
        :rtype: str
        """

        return (
            '<{self.__class__.__name__} ({self.uid}) "{self.name}">'
        ).format(self=self)

    def __eq__(self, other):
        """ Evaluates if two instances are the same.

        .. note:: Name is not taken into consideration for instance equality.

        :returns: A boolean if two instances are the same
        :rtype: bool
        """

        return hash(self) == hash(other)

    def __hash__(self):
        """ Returns an identifying integer for the calling SandPaper instance.

        :returns: An identifying integer for the calling SandPaper instance
        :rtype: int
        """

        return int(self.uid, 16)

    @property
    def name(self):
        """ The descriptive name of the SandPaper instance.

        .. note:: If no name has been given, a continually updating uid hash
            of the active rules is used instead

        :getter: Returns the given or suitable name for a SandPaper instance
        :setter: Sets the descriptive name of the SandPaper instance
        :rtype: str
        """

        if not hasattr(self, '_name'):
            return self.uid
        return self._name

    @name.setter
    def name(self, name):
        """ Sets the descriptive name of the SandPaper instance.

        :param str name: A descriptive name for the SandPaper instance
        :returns: Nothing
        """

        assert isinstance(name, six.string_types) and len(name) > 0, (
            'name expected a string of positive length, received "{name}"'
        ).format(**locals())
        self._name = name

    @property
    def uid(self):
        """ A continually updating hash of the active rules.

        A hexadecimal digest string

        :getter: Returns a continually updating hash of the active rules
        :rtype: str
        """

        hasher = hashlib.md5()
        for (rule, rule_args, rule_kwargs,) in self.rules:
            hasher.update((
                '{rule.__name__}({rule_args}, {rule_kwargs})'
            ).format(**locals()).encode('utf-8'))
        return hasher.hexdigest()

    @property
    def rules(self):
        """ This list of applicable rules for the SandPaper instance.

        :getter: Returns the list of applicable rules for the instance
        :rtype: list[tuple(callable, tuple(....,....), dict[str,....])]
        """

        if not hasattr(self, '_rules'):
            self._rules = []
        return self._rules

    @property
    def value_rules(self):
        """ The set of value rules for the SandPaper instance.

        :getter: Returns the set rules for the SandPaper instance
        :rtype: set(callable)
        """

        if not hasattr(self, '_value_rules'):
            self._value_rules = set()
        return self._value_rules

    @property
    def record_rules(self):
        """ The set of record rules for the SandPaper instance.

        :getter: Returns the set rules for the SandPaper instance
        :rtype: set(callable)
        """

        if not hasattr(self, '_record_rules'):
            self._record_rules = set()
        return self._record_rules

    def _filter_values(
        self, record,
        column_filter=None, value_filter=None, callable_filter=None,
        **kwargs
    ):
        """ Yield only allowed (column, value) pairs using supported filters.

        :param collections.OrderedDict record: An ordered dictionary of
            (``column_name``, ``row_value``) items
        :param str column_filter: A matched regular expression for
            ``column_name``
        :param str value_filter: A matched regular expression for ``row_value``
        :param callable callable_filter: An truthy evaluated callable
        :param dict kwargs: Any named arguments, for the kwargs of
            ``callable_filter``
        :returns: A generator yielding allowed (column, value) pairs
        """

        for (column, value) in record.items():

            if column_filter is not None:
                if not column_filter.match(str(column)):
                    continue
            if value_filter is not None:
                if not value_filter.match(str(value)):
                    continue
            if callable(callable_filter):
                if not callable_filter(record, column, **kwargs):
                    continue

            yield (column, value,)

    def _apply_rules(
        self, from_file,
        sheet_name=None,
        **kwargs
    ):
        """ Base rule application method.

        :param str from_file: The file to apply rules to
        :param str sheet_name: The name of the sheet to apply rules to
        :param dict kwargs: Any named arguments, for the reading of the file
        :returns: Yields normalized records
        """

        for record in pyexcel.iget_records(
            file_name=from_file,
            sheet_name=sheet_name,
            **kwargs
        ):
            # start application of all registered rules
            for (rule, rule_args, rule_kwargs,) in self.rules:
                if rule in self.value_rules:
                    # value rules are  required to pass filtering
                    for (column, value,) in self._filter_values(
                        record, **rule_kwargs
                    ):
                        # handle application of value rule
                        record[column] = rule(
                            self, record.copy(), column,
                            *rule_args, **rule_kwargs
                        )
                else:
                    # handle application of record rule
                    record = rule(
                        self, record.copy(),
                        *rule_args, **rule_kwargs
                    )

            yield record

    def _apply_to(
        self, from_file, to_file,
        sheet_name=None,
        **kwargs
    ):
        """ Threadable rule processing method.

        .. important:: No overwrite protection is enabled for this method.
            If the ``from_file`` is equal to the ``to_file``, then
            ``from_file`` will be overwritten.

        :param str from_file: The input filepath
        :param str to_file: The output filepath
        :param str sheet_name: The name of the sheet to apply rules to
        :param dict kwargs: Any named arguments, passed to ``_apply_rules``
        :returns: The saved normalized filepath
        :rtype: str
        """

        pyexcel.isave_as(
            records=list(self._apply_rules(
                from_file,
                sheet_name=sheet_name,
                **kwargs
            )),
            dest_file_name=to_file,
            lineterminator=os.linesep,
        )
        return to_file

    @value_rule
    def lower(self, record, column, **kwargs):
        """ A basic lowercase rule for a given value.

        Only applies to text type variables

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param dict kwargs: Any named arguments
        :returns: The value lowercased
        """

        value = record[column]
        return (
            value.lower()
            if isinstance(value, six.string_types) else
            value
        )

    @value_rule
    def upper(self, record, column, **kwargs):
        """ A basic uppercase rule for a given value.

        Only applies to text type variables

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param dict kwargs: Any named arguments
        :returns: The value uppercased
        """

        value = record[column]
        return (
            value.upper()
            if isinstance(value, six.string_types) else
            value
        )

    @value_rule
    def capitalize(self, record, column, **kwargs):
        """ A basic capitalization rule for a given value.

        Only applies to text type variables

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param dict kwargs: Any named arguments
        :returns: The value capatilized
        """

        value = record[column]
        return (
            value.capitalize()
            if isinstance(value, six.string_types) else
            value
        )

    @value_rule
    def title(self, record, column, **kwargs):
        """ A basic titlecase rule for a given value.

        Only applies to text type variables

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param dict kwargs: Any named arguments
        :returns: The value titlecased
        """

        value = record[column]
        return (
            value.title()
            if isinstance(value, six.string_types) else
            value
        )

    @value_rule
    def lstrip(self, record, column, content=None, **kwargs):
        """ A basic lstrip rule for a given value.

        Only applies to text type variables.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param str content: The content to strip (defaults to whitespace)
        :param dict kwargs: Any named arguments
        :returns: The value with left content stripped
        """

        value = record[column]
        return (
            value.lstrip(content)
            if isinstance(value, six.string_types) else
            value
        )

    @value_rule
    def rstrip(self, record, column, content=None, **kwargs):
        """ A basic rstrip rule for a given value.

        Only applies to text type variables.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param str content: The content to strip (defaults to whitespace)
        :param dict kwargs: Any named arguments
        :returns: The value with right content stripped
        """

        value = record[column]
        return (
            value.rstrip(content)
            if isinstance(value, six.string_types) else
            value
        )

    @value_rule
    def strip(self, record, column, content=None, **kwargs):
        """ A basic strip rule for a given value.

        Only applies to text type variables.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param str content: The content to strip (defaults to whitespace)
        :param dict kwargs: Any named arguments
        :returns: The value with all content stripped
        """

        value = record[column]
        return (
            value.strip(content)
            if isinstance(value, six.string_types) else
            value
        )

    @value_rule
    def increment(
        self, record, column,
        amount=1,
        **kwargs
    ):
        """ A basic increment rule for a given value.

        Only applies to numeric (int, float) type variables.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param amount: The amount to increment by
        :type amount: int or float
        :param dict kwargs: Any named arguments
        :returns: The value incremented by ``amount``
        """

        value = record[column]
        if isinstance(value, (int, float,)):
            return (value + amount)
        return value

    @value_rule
    def decrement(
        self, record, column,
        amount=1,
        **kwargs
    ):
        """ A basic decrement rule for a given value.

        Only applies to numeric (int, float) type variables.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param amount: The amount to decrement by
        :type amount: int or float
        :param dict kwargs: Any named arguments
        :returns: The value incremented by ``amount``
        """

        value = record[column]
        if isinstance(value, (int, float,)):
            return (value - amount)
        return value

    @value_rule
    def replace(
        self, record, column,
        replacements,
        **kwargs
    ):
        """ Applies a replacements dictionary to a value.

        Take for example the following SandPaper instance:

        .. code-block:: python

            s = SandPaper('my-sandpaper').replace({
                'this_is_going_to_be_replaced': 'with_this',
            })


        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param dict[str, str] replacements: A dictionary of replacements
            for the value
        :param dict kwargs: Any named arguments
        :returns: The value with all replacements made
        """

        value = record[column]
        if isinstance(value, six.string_types):
            for (from_text, to_text,) in replacements.items():
                value = value.replace(from_text, to_text)
        return value

    @value_rule
    def translate_text(
        self, record, column,
        translations,
        **kwargs
    ):
        """ A text translation rule for a given value.

        Take for example the following SandPaper instance:

        .. code-block:: python

            s = SandPaper('my-sandpaper').translate_text({
                r'^group(?P<group_id>\d+)\s*(.*)$': '{group_id}'
            }, column_filter=r'^group_definition$')


        This will translate all instances of the value
        ``group<GROUP NUMBER>`` to ``<GROUP NUMBER>`` only in columns named
        ``group_definition``.

        .. important:: Note that matched groups and matched groupdicts are
            passed as ``*args`` and ``**kwargs`` to the format method of the
            returned ``to_format`` string.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param dict[str, str] translations: A dictionary of translations for
            the value
        :param dict kwargs: Any named arguments
        :returns: The potentially translated value
        """

        value = record[column]

        for (from_regex, to_format,) in translations.items():
            match = regex.match(from_regex, str(value))
            if match is not None:
                # NOTE: Would prefer to use PEP448, but have to do this for PY2
                named_groups = kwargs.copy()
                named_groups.update(match.groupdict())

                # TODO: simplify the passing of arguments to this format
                value = to_format.format(
                    *[
                        (capture if capture is not None else '')
                        for capture in match.groups()
                    ], **{
                        name: (capture if capture is not None else '')
                        for (name, capture) in named_groups.items()
                    }
                )

        return value

    @value_rule
    def translate_date(
        self, record, column,
        translations,
        **kwargs
    ):
        """ A date translation rule for a given value.

        Take for example the following SandPaper instance:

        .. code-block:: python

            s = SandPaper('my-sandpaper').translate_date({
                'YYYY-MM-DD': 'YYYY',
                'YYYY': 'YYYY',
                'YYYY-MM': 'YYYY'
            }, column_filter=r'^(.*)_date$')


        This will translate all instances of a date value matching the given
        date formats in columns ending with ``_date`` to the date format
        ``YYYY``.

        .. important:: Note that the date evaluation is done through the
            `arrow <https://arrow.readthedocs.io/en/latest/>`_ module
            and discovers date formats.

            This could lead to unexpected date changes. For this reason, it is
            implicitly required that at the very least a ``column_filter`` is
            used to filter what columns should be considered. The use of a
            ``value_filter`` would ensure even less false positive date
            evaluation.

        .. note:: Raises a UserWarning if the lack of a ``column_filter`` is
            detected.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param str column: A column that indicates what value to normalize
        :param dict[str, str] translations: A dictionary of translations from
            an arrow based dateformats to a different format
        :param dict kwargs: Any named arguments
        :returns: The value potentially translated value
        """

        value = record[column]
        if isinstance(value, (datetime.date, datetime.datetime,)):
            # FIXME: This isn't my fault but it needs to be fixed
            # pyexcel shouldn't detect this datetime with the __default_apply
            # parameters implicitly passed, but it does...
            return arrow.get(value).format(list(translations.values())[0])

        for (from_format, to_format,) in translations.items():
            try:
                return arrow.get(value, from_format).format(to_format)
            except ValueError:
                continue
        return value

    @record_rule
    def add_columns(self, record, additions, **kwargs):
        """ Adds columns to a record.

        .. note:: If the value of an entry in ``additions`` is a callable,
            then the callable should expect the ``record`` as the only
            parameter and should return the value that should be placed in the
            newly added column.

            If the value of an entry in ``additions`` is a string, the record
            is passed in as kwargs to the value's ``format`` method.

            Otherwise, the value of an entry in ``additions`` is simply used
            as the newly added column's value.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param dict[str,....] additions: A dictionary of column names to
            callables, strings, or other values
        :param dict kwargs: Any named arguments
        :returns: The record with a potential newly added column
        """

        # TODO: add the ability to specify index in the record to add column to
        # column already exists, completely ignore adding it

        for (name, value,) in additions.items():
            if name in record:
                continue

            if callable(value):
                record[name] = value(record)
            elif isinstance(value, six.string_types):
                record[name] = value.format(**record)
            else:
                record[name] = value

        return record

    @record_rule
    def remove_columns(self, record, removes, **kwargs):
        """ Removes columns from a record.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param list[str] removes: A list of columns to remove
        :param dict kwargs: Any named arguments
        :returns: The record with a potential newly removed column
        """

        for name in removes:
            if name in record:
                del record[name]

        return record

    @record_rule
    def rename_columns(
        self, record, renames,
        **kwargs
    ):
        """ Maps an existing column to a new column.

        :param collections.OrderedDict record: A record whose value within
            ``column`` should be normalized and returned
        :param dict[str, str] renames: A dictionary of column to column renames
        :param dict kwargs: Any named arguments
        :returns: The record with the remapped column
        """

        # full OrderedDict rebuild required for column renaming
        return collections.OrderedDict([(
            (renames[key] if key in renames else key),
            value,
        ) for (key, value,) in record.items()])

    @record_rule
    def order_columns(
        self, record, order,
        ignore_missing=False,
        **kwargs
    ):
        """ Orders columns in a specific order.

        :param collections.OrderedDict record: A record who should be ordered
        :param list[str] order: The order that columns need to be in
        :param bool ignore_missing: Boolean which inidicates if missing columns
            from ``order`` should be ignored
        :param dict kwargs: Any named arguments
        :returns: The record with the columns reordered
        """
        ordered_record = collections.OrderedDict([
            (column_name, record[column_name],)
            for column_name in order
            if column_name in record
        ])

        if not ignore_missing:
            for column_name in record:
                if column_name not in order:
                    ordered_record[column_name] = record[column_name]

        return ordered_record

    def apply(
        self, from_file, to_file,
        sheet_name=None,
        **kwargs
    ):
        """ Applies a SandPaper instance rules to a given glob of files.

        :param str from_file: The path of the file to apply the rules to
        :param str to_file: The path of the file to write to
        :param str sheet_name: The name of the sheet to apply rules to
            (defaults to the first available sheet)
        :param dict kwargs: Any additional named arguments
            (applied to the pyexcel ``iget_records`` method)
        :returns: Yields output filepaths (not in any consistent order)
        """

        # precompile filter regexes (kinda speeds up the processing)
        for (rule, rule_args, rule_kwargs,) in self.rules:
            for (key, value,) in rule_kwargs.items():
                if key in self.__available_filters and \
                        isinstance(value, six.string_types):
                    rule_kwargs[key] = regex.compile(value)

        try:
            return self._apply_to(
                from_file, to_file,
                sheet_name=sheet_name,
                **dict(self.__default_apply, **kwargs)
            )
        finally:
            pyexcel.free_resources()
