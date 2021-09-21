'''Module to create object from a fortran namelist
'''

from pathlib import Path
import os
import re
import sys

try:
    from collections import OrderedDict
except ImportError:
    sys.exit('need OrderedDict from collections module')


__all__ = ['MESA', 'MESAdefaults']



def format_value_to_fortran(value):
    '''recieves a python-friendly value and returns it into an appropiate fortran one
    '''
    is_python2 = sys.version_info < (3,0,0)
    if isinstance(value, bool):
        return value and '.true.' or '.false.'
    elif isinstance(value, int):
        return '{:d}'.format(value)
    elif isinstance(value, float):
        return ('{:.2e}'.format(value)).replace('e','d')
    elif isinstance(value, str):
        return "'{}'".format(value)
    elif is_python2 and isinstance(value, unicode):  # needed if unicode literals are used
        return "'{}'".format(value)
    elif isinstance(value, complex):
        return '({},{})'.format(format_value_to_fortran(value.real),
                format_value_to_fortran(value.imag))
    else:
        raise Exception("Variable type not understood: {}".format(type(value)))


def dump(cls, namelist='', array_inline=True):
    '''create string to be dumped into a fortran namelist from the attributes of a given
    class of namelist elements'''
    lines = ["&{}".format(namelist)]
    for variable_name, variable_value in cls.groups[namelist].items():
        if isinstance(variable_value, list):
            if array_inline:
                lines.append('   {} = {}'.format(variable_name, ' '.join([format_value_to_fortran(v)
                    for v in variable_value])))
            else:
                for n, v in enumerate(variable_value):
                    lines.append('   {}({:d}) = {}'.format(variable_name, n+1, format_value_to_fortran(v)))
        else:
            lines.append('   {} = {}'.format(variable_name, format_value_to_fortran(variable_value)))
    lines.append('/ ! end of {} namelist'.format(namelist))

    return '\n'.join(lines) + '\n'


class AttributeMapper():
    '''simple mapper to access dictionary items as attributes
    '''

    def __init__(self, obj):
        self.__dict__['data'] = obj

    def __getattr__(self, attr):
        if attr in self.data:
            found_attr = self.data[attr]
            if isinstance(found_attr, dict):
                return AttributeMapper(found_attr)
            else:
                return found_attr
        else:
            raise AttributeError

    def __setattr__(self, attr, value):
        if attr in self.data:
            self.data[attr] = value
        else:
            raise NotImplementedError

    def __dir__(self):
        return self.data.keys()


class NoSingleValueFoundException(Exception):
    pass


class Namelist():
    '''Parses namelist files in Fortran 90 format, recognised groups are
    available through 'groups' attribute'''

    def __init__(self, input_str='', name=''):

        if name == '' and input_str == '': return

        self.groups = OrderedDict()

        group_re = re.compile(r'&([^&]+)/', re.DOTALL)  # allow blocks to span multiple lines
        array_re = re.compile(r'(\w+)\((\d+)\)')
        string_re = re.compile(r"\'\s*\w[^']*\'")
        self._complex_re = re.compile(r'^\((\d+.?\d*),(\d+.?\d*)\)$')

        filtered_lines = []
        for line in input_str.split('\n'):
            if line.strip().startswith('!'):
                continue
            else:
                filtered_lines.append(line)

        group_blocks = re.findall(group_re, "\n".join(filtered_lines))

        group_cnt = {}

        for group_block in group_blocks:
            block_lines_raw = group_block.split('\n')
            group_name = block_lines_raw.pop(0).strip()

            group = OrderedDict()

            block_lines = []
            for line in block_lines_raw:
                # cleanup string
                line = line.strip()
                if line == "":
                    continue
                if line.startswith('!'):
                    continue

                try:
                    k, v = line.split('=')
                    block_lines.append(line)
                except ValueError:
                    # no = in current line, try to append to previous line
                    if block_lines[-1].endswith(','):
                        block_lines[-1] += line
                    else:
                        raise

            for line in block_lines:
                # commas at the end of lines seem to be optional
                if line.endswith(','):
                    line = line[:-1]

                # inline comments are allowed, but we remove them for now
                if "!" in line:
                    line = line.split("!")[0].strip()

                k, v = line.split('=')
                variable_name = k.strip()
                variable_value = v.strip()

                variable_name_groups = re.findall(array_re, k)

                variable_index = None
                if len(variable_name_groups) == 1:
                    variable_name, variable_index = variable_name_groups[0]
                    variable_index = int(variable_index)-1 # python indexing starts at 0

                try:
                    parsed_value = self._parse_value(variable_value)

                    if variable_index is None:
                        group[variable_name] = parsed_value
                    else:
                        if not variable_name in group:
                            group[variable_name] = {'_is_list': True}
                        group[variable_name][variable_index] = parsed_value

                except NoSingleValueFoundException as e:
                    # see we have several values inlined
                    if variable_value.count("'") in [0, 2]:
                        if variable_value.count('(') != 0:  # if list of complex values
                            variable_arr_entries = variable_value.split()
                        else:
                            # replacing ',' makes comma-separated arrays possible,
                            # see unit test test_inline_array_comma
                            # this fails if an array of complex numbers is comma-separated
                            variable_arr_entries = variable_value.replace(',', ' ').split()
                    else:
                        # we need to be more careful with lines with escaped
                        # strings, since they might contained spaces
                        matches = re.findall(string_re, variable_value)
                        variable_arr_entries = [s.strip() for s in matches]


                    for variable_index, inline_value in enumerate(variable_arr_entries):
                        parsed_value = self._parse_value(inline_value)

                        if variable_index is None:
                            group[variable_name] = parsed_value
                        else:
                            if not variable_name in group:
                                group[variable_name] = {'_is_list': True}
                            group[variable_name][variable_index] = parsed_value

            if group_name in self.groups.keys():

                if not group_name in group_cnt.keys():
                    group_cnt[group_name] = 0
                else:
                    group_cnt[group_name] += 1
                group_name = group_name + str(group_cnt[group_name])

            self.groups[group_name] = group
            self._check_lists()

    def _parse_value(self, variable_value):
        '''tries to parse a single value, raises an exception if no single value is matched
        '''
        try:
            parsed_value = int(variable_value)
        except ValueError:
            try:
                tmp = variable_value.replace('d','E')
                parsed_value = float(tmp)
            except ValueError:
                # check for complex number
                complex_values = re.findall(self._complex_re, variable_value)
                if len(complex_values) == 1:
                    a, b = complex_values[0]
                    parsed_value = complex(float(a),float(b))
                elif variable_value in ['.true.', 'T']:
                    # check for a boolean
                    parsed_value = True
                elif variable_value in ['.false.', 'F']:
                    parsed_value = False
                else:
                    # see if we have an escaped string
                    if variable_value.startswith("'") and variable_value.endswith("'") and \
                            variable_value.count("'") == 2:
                        parsed_value = variable_value[1:-1]
                    elif variable_value.startswith('"') and variable_value.endswith('"') and \
                            variable_value.count('"') == 2:
                        parsed_value = variable_value[1:-1]
                    else:
                        raise NoSingleValueFoundException(variable_value)

        return parsed_value

    def _check_lists(self):
        for group in self.groups.values():
            for variable_name, variable_values in group.items():
                if isinstance(variable_values, dict):
                    if '_is_list' in variable_values and variable_values['_is_list']:
                        variable_data = variable_values
                        del(variable_data['_is_list'])

                        num_entries = len(variable_data.keys())
                        variable_list = [None]*num_entries

                        for i, value in variable_data.items():
                            if i >= num_entries:
                                raise Exception("""The variable '{}' has an array index
                                assignment that is inconsistent with the number of list
                                values""".format(variable))
                            else:
                                variable_list[i] = value

                        group[variable_name] = variable_list


class MESA(Namelist):
    '''MESA namelists into python-friendly object
    '''

    def __init__(self, input_str=''):
        # inherit Namelist __init__ method
        Namelist.__init__(self, input_str)

    @property
    def star_job(self):
        if 'star_job' in self.groups.keys():
            return AttributeMapper(self.groups['star_job'])
        else:
            return None

    @property
    def controls(self):
        if 'controls' in self.groups.keys():
            return AttributeMapper(self.groups['controls'])
        else:
            return None

    @property
    def pgstar(self):
        if 'pgstar' in self.groups.keys():
            return AttributeMapper(self.groups['pgstar'])
        else:
            return None

    @property
    def binary_job(self):
        if 'binary_job' in self.groups.keys():
            return AttributeMapper(self.groups['binary_job'])
        else:
            return None

    @property
    def binary_controls(self):
        if 'binary_controls' in self.groups.keys():
            return AttributeMapper(self.groups['binary_controls'])
        else:
            return None


class MESAdefaults(object):
    '''MESA defaults values for all namelists
    '''

    def __init__(self, mesa_dir=''):

        self.mesa_dir = mesa_dir
        if self.mesa_dir is None:
            raise ValueError('MESA_DIR is not set')

        self.groups = OrderedDict()

        self.groups['star_job'] = self._get_defaults(namelist='star_job')
        self.groups['controls'] = self._get_defaults(namelist='controls')
        self.groups['pgstar'] = self._get_defaults(namelist='pgstar')
        self.groups['binary_job'] = self._get_defaults(namelist='binary_job')
        self.groups['binary_controls'] = self._get_defaults(namelist='binary_controls')


    def _get_defaults(self, namelist=''):

        if namelist in ('star_job', 'controls', 'pgstar'):
            fname = Path(self.mesa_dir) / 'star/defaults' / '{}.defaults'.format(namelist)
        else:
            fname = Path(self.mesa_dir) / 'binary/defaults' / '{}.defaults'.format(namelist)

        with open(str(fname), 'r') as f:
            lines = [line.strip() for line in f.readlines() if len(line) > 0]

        elements = OrderedDict()
        for k,line in enumerate(lines):
            if not line.startswith("!") and '=' in line:
                line = line.split('!',1)[0]
                if len(line.split('=')) < 2:
                    print(line)
                    raise ValueError('error in line: %s' % line)
                elif len(line.split('=')) > 2:  # there is just one string in the defaults with two '='
                    name, lval, rval = line.split('=')
                    value = '{}={}'.format(lval, rval)
                else:
                    name, value, *extraWords = line.split('=')
                elements[name.strip()] = self._parse_value(value.strip())
        return elements

    def _parse_value(self, variable_value):
        '''tries to parse a single value, raises an exception if no single value is matched'''
        try:
            parsed_value = int(variable_value)
        except ValueError:
            try:
                tmp = variable_value.replace('d','E')
                parsed_value = float(tmp)
            except ValueError:
                if variable_value in ['.true.', 'T']:
                    # check for a boolean
                    parsed_value = True
                elif variable_value in ['.false.', 'F']:
                    parsed_value = False
                else:
                    # see if we have an escaped string
                    if variable_value.startswith("'") and variable_value.endswith("'") \
                            and variable_value.count("'") == 2:
                        parsed_value = variable_value[1:-1]
                    elif variable_value.startswith('"') and variable_value.endswith('"') \
                            and variable_value.count('"') == 2:
                        parsed_value = variable_value[1:-1]
                    else:
                        raise NoSingleValueFoundException(variable_value)
        return parsed_value

    @property
    def star_job(self):
        if 'star_job' in self.groups.keys():
            return AttributeMapper(self.groups['star_job'])
        else:
            return None

    @property
    def controls(self):
        if 'controls' in self.groups.keys():
            return AttributeMapper(self.groups['controls'])
        else:
            return None

    @property
    def pgstar(self):
        if 'pgstar' in self.groups.keys():
            return AttributeMapper(self.groups['pgstar'])
        else:
            return None

    @property
    def binary_job(self):
        if 'binary_job' in self.groups.keys():
            return AttributeMapper(self.groups['binary_job'])
        else:
            return None

    @property
    def binary_controls(self):
        if 'binary_controls' in self.groups.keys():
            return AttributeMapper(self.groups['binary_controls'])
        else:
            return None
