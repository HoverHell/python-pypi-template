#!/usr/bin/env python
# pylint: disable=missing-docstring

import fnmatch
import os
from os.path import abspath, dirname, join
import re
import sys
import textwrap
from datetime import date
from string import capwords
from subprocess import check_call, check_output
from configparser import ConfigParser


THIS_DIR = dirname(abspath(__file__))
TEMPLATE_DIR = join(THIS_DIR, 'template')
POPULATE_INI = join(THIS_DIR, 'populate.ini')


def find_templated_files():
    for root, _, filenames in os.walk(TEMPLATE_DIR):
        for filename in fnmatch.filter(filenames, '*.template'):
            yield join(root, filename)


def find_templated_directories():
    for root, dirnames, _ in os.walk(TEMPLATE_DIR):
        for subdir in fnmatch.filter(dirnames, '{{ * }}'):
            yield join(root, subdir)


def git(*args):
    return check_output(('git',) + tuple(args), encoding='utf-8').strip()


def read_requirements(requirements_file_basename):
    with open(join(TEMPLATE_DIR, requirements_file_basename)) as fobj:
        content = fobj.read()

    for line in content.splitlines():
        line = re.sub('#.*', '', line).strip()
        if not line:
            continue
        # Ignore included requirements files.
        if line.startswith('-r'):
            continue
        yield line


def get_author_email():
    return git('config', 'user.email')


def get_author_name():
    return git('config', 'user.name')


def get_copyright_years():
    return str(date.today().year)


def get_github_info():
    result = re.search(
        'github.com[:/](?P<user>[^/]+)/(?P<repo>.+)(?:[.]git)?$',
        git('config', '--get', 'remote.origin.url'))

    if not result:
        raise RuntimeError(
            'Failed to find a GitHub user and/or repository name in the '
            'output of "git config --get remote.origin.url".')

    return result.group('user'), result.group('repo')


def get_install_requires():
    return tuple(read_requirements('requirements.txt'))


def get_tests_require():
    return tuple(read_requirements('requirements_test.txt'))


def get_populate_ini_settings():
    config = ConfigParser()
    with open(POPULATE_INI) as fobj:
        config.read_file(fobj)
    values = dict(
        package_name=config.get('global', 'package_name'),
        package_version=config.get('global', 'package_version'),
        short_description=config.get('global', 'short_description'))

    empty_values = [key for key in values if not key]

    if empty_values:
        raise RuntimeError(
            'Please specify values in "populate.ini" for the following: '
            '{empty}'.format(empty=empty_values))

    return values


def get_template_values():
    user, repo = get_github_info()

    values = dict(
        author_email=get_author_email(),
        author_name=get_author_name(),
        copyright_years=get_copyright_years(),
        github_user=user,
        repo_name=repo,
        install_requires=get_install_requires(),
        tests_require=get_tests_require())

    values.update(get_populate_ini_settings())

    # The actual package directory should not have dashes in it, but dashes are
    # pretty common for package names.
    values['package_dir_name'] = values['package_name'].replace('-', '_')

    print('Using the following template values:\n{values}'.format(
        values='\n'.join(
            '    {key}: {value!r}'.format(key=key, value=value)
            for key, value in values.items())))

    return values


def replace_multiline(text, key, value, filter_name, replacement_fn):
    token = '{{{{ {k}|{f} }}}}'.format(k=key, f=filter_name)
    regex = re.compile('(?P<indent>[ \t]*){token}'.format(
        token=re.escape(token)))

    while True:
        match = regex.search(text)

        if not match:
            break

        indent = match.group('indent')
        replacement = replacement_fn(indent=indent, value=value)
        text = regex.sub(replacement, text, count=1)

    return text


def replace_pystrings(text, key, value):
    return replace_multiline(
        text=text, key=key, value=value,
        filter_name='pystring',
        replacement_fn=lambda indent, value: indent + ('\n' + indent).join(
            repr(line) for line in textwrap.wrap(
                value,
                drop_whitespace=False,
                width=70 - len(indent))))


def replace_pytuples(text, key, value):
    text = replace_multiline(
        text=text, key=key, value=value,
        filter_name='pytuple',
        replacement_fn=lambda indent, value: '\n'.join(
            indent + repr(v) + ',' for v in value))

    # squash empty tuples
    text = re.sub(r'[(]\s+[)]', '()', text)

    return text


def replace_raw(text, key, value):
    return text.replace('{{{{ {k} }}}}'.format(k=key), value)


def replace_capitalize(text, key, value):
    return text.replace(
        '{{{{ {k}|capitalize }}}}'.format(k=key), capwords(value))


def do_replacements(text, key, value, fns):
    for func in fns:
        text = func(text=text, key=key, value=value)
    return text


def populate_files(template_values):
    for template_path in find_templated_files():
        with open(template_path) as fobj:
            content = fobj.read()

        for key, value in template_values.items():
            if isinstance(value, str):
                fns = (replace_raw, replace_capitalize, replace_pystrings)
            else:
                fns = (replace_pytuples,)

            content = do_replacements(
                text=content, key=key, value=value, fns=fns)

        with open(template_path, 'w') as fobj:
            fobj.write(content)

        populated_path = re.sub('[.]template$', '', template_path)
        git('mv', '-f', template_path, populated_path)


def populate_directories(template_values):
    for template_subdir in find_templated_directories():

        for key, value in template_values.items():
            if not isinstance(value, str):
                continue

            renamed_dir = template_subdir.replace(
                '{{{{ {key} }}}}'.format(key=key),
                value)

            if renamed_dir != template_subdir:
                git('mv', template_subdir, renamed_dir)


def main():
    try:
        template_values = get_template_values()
        populate_files(template_values)
        populate_directories(template_values)

        # No longer need the template setup files!
        git('rm', '-f', POPULATE_INI)
        git('rm', '-f', abspath(__file__))

        # Move everything in template/ to the root of the project.
        for filename in os.listdir(TEMPLATE_DIR):
            git('mv', '-f', join(TEMPLATE_DIR, filename), THIS_DIR)

        # The template dir is unneeded now and should be empty.
        check_call(('rmdir', TEMPLATE_DIR))

        # Stage the rest of the updated files.
        git('add', '-u')

    except RuntimeError as exc:
        print('[ERROR] {exc}'.format(exc=exc))
        sys.exit(1)


if __name__ == '__main__':
    main()
