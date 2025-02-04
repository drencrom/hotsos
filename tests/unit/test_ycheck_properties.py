import datetime
import os
import shutil
import tempfile
from unittest import mock

import yaml
from hotsos.core.config import HotSOSConfig
from hotsos.core.host_helpers.config import SectionalConfigBase
from hotsos.core.issues.utils import IssuesStore
from hotsos.core.ycheck import scenarios
from hotsos.core.ycheck.engine import (
    YDefsSection,
    YDefsLoader,
)
from hotsos.core.ycheck.engine.properties.common import (
    YPropertyBase,
    PropertyCacheRefResolver,
    cached_yproperty_attr,
)
from hotsos.core.ycheck.engine.properties.search import YPropertySearch
from hotsos.core.ycheck.engine.properties.requires.types import (
    apt,
    snap,
)

from . import utils


class TestProperty(YPropertyBase):

    @cached_yproperty_attr
    def myattr(self):
        return '123'

    @property
    def myotherattr(self):
        return '456'

    @property
    def always_true(self):
        return True

    @property
    def always_false(self):
        return False


class TestConfig(SectionalConfigBase):
    pass


class FakeServiceObjectManager(object):

    def __init__(self, start_times):
        self._start_times = start_times

    def __call__(self, name, state, has_instances):
        return FakeServiceObject(name, state, has_instances,
                                 start_time=self._start_times[name])


class FakeServiceObject(object):

    def __init__(self, name, state, has_instances, start_time):
        self.name = name
        self.state = state
        self.start_time = start_time
        self.has_instances = has_instances


YAML_DEF_REQUIRES_APT = """
pluginX:
  groupA:
    requires:
      apt:
        mypackage:
          - min: '3.0'
            max: '3.2'
          - min: '4.0'
            max: '4.2'
          - min: '5.0'
            max: '5.2'
        altpackage:
          - min: '3.0'
            max: '3.2'
          - min: '4.0'
            max: '4.2'
          - min: '5.0'
            max: '5.2'
"""

YAML_DEF_REQUIRES_PEBBLE_FAIL = """
pluginX:
  groupA:
    requires:
      pebble:
        foo:
          state: active
"""


YAML_DEF_REQUIRES_SYSTEMD_PASS_1 = """
pluginX:
  groupA:
    requires:
      systemd:
        ondemand: enabled
        nova-compute: enabled
"""


YAML_DEF_REQUIRES_SYSTEMD_STARTED_AFTER = """
pluginX:
  groupA:
    requires:
      systemd:
        openvswitch-switch:
          state: enabled
          started-after: neutron-openvswitch-agent
"""


YAML_DEF_REQUIRES_SYSTEMD_FAIL_1 = """
pluginX:
  groupA:
    requires:
      systemd:
        ondemand: enabled
        nova-compute: disabled
"""

YAML_DEF_REQUIRES_MAPPED = """
checks:
  is_exists_mapped:
    systemd: nova-compute
  is_exists_unmapped:
    requires:
      systemd: nova-compute
conclusions:
"""

YAML_DEF_REQUIRES_SYSTEMD_FAIL_2 = """
pluginX:
  groupA:
    requires:
      systemd:
        ondemand:
          state: enabled
        nova-compute:
          state: disabled
          op: eq
"""


YAML_DEF_REQUIRES_GROUPED = """
passdef1:
  requires:
    - path: sos_commands/networking
    - not:
        path: sos_commands/networking_foo
    - apt: python3.8
    - and:
        - apt:
            systemd:
              - min: '245.4-4ubuntu3.14'
                max: '245.4-4ubuntu3.15'
      or:
        - apt: nova-compute
      not:
        - apt: blah
passdef2:
  requires:
    and:
      - apt: systemd
    or:
      - apt: nova-compute
    not:
      - apt: blah
faildef1:
  requires:
    - path: sos_commands/networking_foo
    - and:
        - apt: doo
        - apt: daa
      or:
        - apt: nova-compute
      not:
        - and:
            - apt: 'blah'
        - and:
            - apt: nova-compute
faildef2:
  requires:
    - apt: python3.8
    - apt: python1.0
"""

DPKG_L = """
ii  openssh-server                       1:8.2p1-4ubuntu0.4                                   amd64        secure shell (SSH) server, for secure access from remote machines
"""  # noqa


class TempScenarioDefs(object):

    def __init__(self):
        self.root = None
        self.path = None

    def __enter__(self):
        self.root = tempfile.mkdtemp()
        HotSOSConfig.plugin_yaml_defs = self.root
        self.path = os.path.join(self.root, 'scenarios',
                                 HotSOSConfig.plugin_name, 'test.yaml')
        os.makedirs(os.path.dirname(self.path))
        return self

    def __exit__(self, *args):
        shutil.rmtree(self.root)
        return False


class TestYamlRequiresTypeCache(utils.BaseTestCase):

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': 'ii foo 123 amd64'})
    def test_single_item(self):
        mydef = YDefsSection('mydef', yaml.safe_load("chk1:\n  apt: foo"))
        for entry in mydef.leaf_sections:
            self.assertTrue(entry.requires.passes)
            expected = {'__PREVIOUSLY_CACHED_PROPERTY_TYPE':
                        'YRequirementTypeAPT',
                        'package': 'foo',
                        'passes': True,
                        'version': '123'}
            self.assertEqual(entry.requires.cache.data, expected)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l':
                             'ii foo 123 amd64'})
    def test_grouped_items_first_true(self):
        """ If the first item evaluates to True we get that one. """
        scenario = """
        checks:
          c1:
            or:
              - apt: foo
              - apt: bar
        conclusions:
          c1:
            decision: c1
            raises:
              type: SystemWarning
              message: '{pkg}'
              format-dict:
                pkg: '@checks.c1.requires.package'
        """  # noqa
        with TempScenarioDefs() as tmpscenarios:
            with open(tmpscenarios.path, 'w') as fd:
                fd.write(scenario)

            scenarios.YScenarioChecker().load_and_run()
            issues = list(IssuesStore().load().values())[0]
            self.assertEqual(issues[0]['message'], 'foo')

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l':
                             'ii bar 123 amd64'})
    def test_grouped_items_last_true(self):
        """ If the last item evaluates to True we get that one. """
        scenario = """
        checks:
          c1:
            or:
              - apt: foo
              - apt: bar
        conclusions:
          c1:
            decision: c1
            raises:
              type: SystemWarning
              message: '{pkg}'
              format-dict:
                pkg: '@checks.c1.requires.package'
        """  # noqa
        with TempScenarioDefs() as tmpscenarios:
            with open(tmpscenarios.path, 'w') as fd:
                fd.write(scenario)

            scenarios.YScenarioChecker().load_and_run()
            issues = list(IssuesStore().load().values())[0]
            self.assertEqual(issues[0]['message'], 'bar')

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l':
                             'ii foo 123 amd64\nii bar 123 amd64'})
    def test_grouped_items_all_true(self):
        """ If all items evaluate to True we get the cache of the last one. """
        scenario = """
        checks:
          c1:
            or:
              - apt: foo
              - apt: bar
        conclusions:
          c1:
            decision: c1
            raises:
              type: SystemWarning
              message: '{pkg}'
              format-dict:
                pkg: '@checks.c1.requires.package'
        """  # noqa
        with TempScenarioDefs() as tmpscenarios:
            with open(tmpscenarios.path, 'w') as fd:
                fd.write(scenario)

            scenarios.YScenarioChecker().load_and_run()
            issues = list(IssuesStore().load().values())[0]
            self.assertEqual(issues[0]['message'], 'bar')

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': ''})
    def test_grouped_items_all_false(self):
        """ If the all items evaluates to False nothing is copied. """
        scenario = """
        checks:
          c1:
            or:
              - apt: foo
              - apt: bar
        conclusions:
          c1:
            decision: c1
            raises:
              type: SystemWarning
              message: '{pkg}'
              format-dict:
                pkg: '@checks.c1.requires.package'
        """
        with TempScenarioDefs() as tmpscenarios:
            with open(tmpscenarios.path, 'w') as fd:
                fd.write(scenario)

            scenarios.YScenarioChecker().load_and_run()
            self.assertEqual(len(IssuesStore().load()), 0)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l':
                             'ii foo 123 amd64\nii bar 123 amd64',
                             'sos_commands/snap/snap_list_--all':
                             'snapd 2.54.2 14549 latest/stable xxx'})
    def test_grouped_items_all_true_mixed_types_apt_first(self):
        """ If the all items evaluates to False nothing is copied. """
        scenario = """
        checks:
          c1:
            or:
              - apt: foo
              - apt: bar
              - snap: snapd
        conclusions:
          c1:
            decision: c1
            raises:
              type: SystemWarning
              message: '{pkg}'
              format-dict:
                pkg: '@checks.c1.requires.package'
        """
        with TempScenarioDefs() as tmpscenarios:
            with open(tmpscenarios.path, 'w') as fd:
                fd.write(scenario)

            scenarios.YScenarioChecker().load_and_run()
            issues = list(IssuesStore().load().values())[0]
            self.assertEqual(issues[0]['message'], 'bar')

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l':
                             'ii foo 123 amd64\nii bar 123 amd64',
                             'sos_commands/snap/snap_list_--all':
                             'snapd 2.54.2 14549 latest/stable xxx'})
    def test_grouped_items_all_true_mixed_types_snap_first(self):
        """ If the all items evaluates to False nothing is copied. """
        scenario = """
        checks:
          c1:
            or:
              - snap: snapd
              - apt: foo
              - apt: bar
        conclusions:
          c1:
            decision: c1
            raises:
              type: SystemWarning
              message: '{pkg}'
              format-dict:
                pkg: '@checks.c1.requires.package'
        """
        with TempScenarioDefs() as tmpscenarios:
            with open(tmpscenarios.path, 'w') as fd:
                fd.write(scenario)

            scenarios.YScenarioChecker().load_and_run()
            issues = list(IssuesStore().load().values())[0]
            # NOTE: dicts and lists are currently being evaluated in
            #       alphabetical order. Not clear why but that explains why
            #       grouped items give this (unexpected) result.
            self.assertEqual(issues[0]['message'], 'bar')


class TestYamlRequiresTypeAPT(utils.BaseTestCase):

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': DPKG_L})
    def test_apt_check_item_package_version_within_ranges_true(self):
        ci = apt.APTCheckItems('ssh')
        result = ci.package_version_within_ranges('openssh-server',
                                                  [{'min': '1:8.2',
                                                    'max': '1:8.3'}])
        self.assertTrue(result)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': DPKG_L})
    def test_apt_check_item_package_version_within_ranges_false(self):
        ci = apt.APTCheckItems('ssh')
        result = ci.package_version_within_ranges('openssh-server',
                                                  [{'min': '1:8.2',
                                                    'max': '1:8.2'}])
        self.assertFalse(result)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': DPKG_L})
    def test_apt_check_item_package_version_within_ranges_multi(self):
        ci = apt.APTCheckItems('ssh')
        result = ci.package_version_within_ranges('openssh-server',
                                                  [{'min': '1:8.0',
                                                    'max': '1:8.1'},
                                                   {'min': '1:8.2',
                                                    'max': '1:8.3'}])
        self.assertTrue(result)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': DPKG_L})
    def test_apt_check_item_package_version_within_ranges_no_max_true(self):
        ci = apt.APTCheckItems('ssh')
        result = ci.package_version_within_ranges('openssh-server',
                                                  [{'min': '1:8.0'},
                                                   {'min': '1:8.1'},
                                                   {'min': '1:8.2'},
                                                   {'min': '1:8.3'}])
        self.assertTrue(result)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': DPKG_L})
    def test_apt_check_item_package_version_within_ranges_no_max_false(self):
        ci = apt.APTCheckItems('ssh')
        result = ci.package_version_within_ranges('openssh-server',
                                                  [{'min': '1:8.3'},
                                                   {'min': '1:8.4'},
                                                   {'min': '1:8.5'}])
        self.assertFalse(result)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': DPKG_L})
    def test_apt_check_item_package_version_within_ranges_mixed_true(self):
        ci = apt.APTCheckItems('ssh')
        result = ci.package_version_within_ranges('openssh-server',
                                                  [{'min': '1:8.0'},
                                                   {'min': '1:8.1',
                                                    'max': '1:8.1.1'},
                                                   {'min': '1:8.2'},
                                                   {'min': '1:8.3'}])
        self.assertTrue(result)

    @utils.create_data_root({'sos_commands/dpkg/dpkg_-l': DPKG_L})
    def test_apt_check_item_package_version_within_ranges_mixed_false(self):
        ci = apt.APTCheckItems('ssh')
        result = ci.package_version_within_ranges('openssh-server',
                                                  [{'min': '1:8.0'},
                                                   {'min': '1:8.1',
                                                    'max': '1:8.1.1'},
                                                   {'min': '1:8.2',
                                                    'max': '1:8.2'},
                                                   {'min': '1:8.3'}])
        self.assertFalse(result)


class TestYamlRequiresTypeSnap(utils.BaseTestCase):

    def test_snap_revision_within_ranges_no_channel_true(self):
        ci = snap.SnapCheckItems('core20')
        result = ci.package_info_matches('core20', [{'min': '1327',
                                                     'max': '1328'}])
        self.assertTrue(result)

    def test_snap_revision_within_ranges_no_channel_false(self):
        ci = snap.SnapCheckItems('core20')
        result = ci.package_info_matches('core20', [{'min': '1326',
                                                     'max': '1327'}])
        self.assertFalse(result)

    def test_snap_revision_within_ranges_channel_true(self):
        ci = snap.SnapCheckItems('core20')
        result = ci.package_info_matches('core20',
                                         [{'min': '1327',
                                           'max': '1328',
                                           'channel': 'latest/stable'}])
        self.assertTrue(result)

    def test_snap_revision_within_ranges_channel_false(self):
        ci = snap.SnapCheckItems('core20')
        result = ci.package_info_matches('core20', [{'min': '1327',
                                                     'max': '1328',
                                                     'channel': 'foo'}])
        self.assertFalse(result)

    def test_snap_revision_within_multi_ranges_channel_true(self):
        ci = snap.SnapCheckItems('core20')
        result = ci.package_info_matches('core20', [{'min': '1326',
                                                     'max': '1327',
                                                     'channel': 'foo'},
                                                    {'min': '1327',
                                                     'max': '1328',
                                                     'channel':
                                                     'latest/stable'},
                                                    {'min': '1329',
                                                     'max': '1330',
                                                     'channel': 'bar'}])
        self.assertTrue(result)

    def test_snap_revision_with_incomplete_range(self):
        with self.assertRaises(Exception):
            snap.SnapCheckItems('core20').package_info_matches('core20',
                                                               [{'min':
                                                                 '1327'}])


class TestYamlProperties(utils.BaseTestCase):

    def test_yproperty_attr_cache(self):
        p = TestProperty()
        self.assertIsNone(getattr(p.cache, '__yproperty_attr__myattr'))
        self.assertEqual(p.myattr, '123')
        self.assertEqual(getattr(p.cache, '__yproperty_attr__myattr'), '123')
        self.assertEqual(p.myattr, '123')
        self.assertEqual(p.myotherattr, '456')

    def test_yaml_def_requires_grouped(self):
        mydef = YDefsSection('mydef',
                             yaml.safe_load(YAML_DEF_REQUIRES_GROUPED))
        tested = 0
        for entry in mydef.leaf_sections:
            if entry.name == 'passdef1':
                tested += 1
                self.assertTrue(entry.requires.passes)
            elif entry.name == 'passdef2':
                tested += 1
                self.assertTrue(entry.requires.passes)
            elif entry.name == 'faildef1':
                tested += 1
                self.assertFalse(entry.requires.passes)
            elif entry.name == 'faildef2':
                tested += 1
                self.assertFalse(entry.requires.passes)

        self.assertEqual(tested, 4)

    def test_get_datetime_from_result(self):
        result = mock.MagicMock()
        result.get.side_effect = lambda idx: _result.get(idx)

        _result = {1: '2022-01-06', 2: '12:34:56.123'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertEqual(ts, datetime.datetime(2022, 1, 6, 12, 34, 56))

        _result = {1: '2022-01-06', 2: '12:34:56'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertEqual(ts, datetime.datetime(2022, 1, 6, 12, 34, 56))

        _result = {1: '2022-01-06'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertEqual(ts, datetime.datetime(2022, 1, 6, 0, 0))

        _result = {1: '2022-01-06 12:34:56.123'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertEqual(ts, datetime.datetime(2022, 1, 6, 12, 34, 56))

        _result = {1: '2022-01-06 12:34:56'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertEqual(ts, datetime.datetime(2022, 1, 6, 12, 34, 56))

        _result = {1: '2022-01-06'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertEqual(ts, datetime.datetime(2022, 1, 6, 0, 0))

        _result = {1: '2022-01-06', 2: 'foo'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertIsNone(ts)

        _result = {1: 'foo'}
        ts = YPropertySearch.get_datetime_from_result(result)
        self.assertIsNone(ts)

    @utils.create_data_root({'mytype/myplugin/defs.yaml':
                             'foo: bar\n',
                             'mytype/myplugin/mytype.yaml':
                             'requires:\n  property: foo\n'})
    def test_fs_override_inheritance(self):
        """
        When a directory is used to group definitions and overrides are
        provided in a <dirname>.yaml file, we need to make sure those overrides
        do not supersceded overrides of the same type used by definitions in
        the same directory.
        """
        HotSOSConfig.set(plugin_yaml_defs=HotSOSConfig.data_root,
                         plugin_name='myplugin')
        expected = {'mytype': {
                        'requires': {
                            'property': 'foo'}},
                    'defs': {'foo': 'bar'}}
        self.assertEqual(YDefsLoader('mytype').plugin_defs,
                         expected)

    @utils.create_data_root({'mytype/myplugin/defs.yaml':
                             'requires:\n  apt: apackage\n',
                             'mytype/myplugin/mytype.yaml':
                             'requires:\n  property: foo\n'})
    def test_fs_override_inheritance2(self):
        """
        When a directory is used to group definitions and overrides are
        provided in a <dirname>.yaml file, we need to make sure those overrides
        do not supersceded overrides of the same type used by definitions in
        the same directory.
        """
        HotSOSConfig.set(plugin_yaml_defs=HotSOSConfig.data_root,
                         plugin_name='myplugin')
        expected = {'mytype': {
                        'requires': {
                            'property': 'foo'}},
                    'defs': {
                        'requires': {
                            'apt': 'apackage'}}}
        self.assertEqual(YDefsLoader('mytype').plugin_defs,
                         expected)

    @mock.patch('hotsos.core.plugins.openstack.OpenstackChecksBase')
    def test_requires_grouped(self, mock_plugin):
        mock_plugin.return_value = mock.MagicMock()
        r1 = {'property':
              'hotsos.core.plugins.openstack.OpenstackChecksBase.r1'}
        r2 = {'property':
              'hotsos.core.plugins.openstack.OpenstackChecksBase.r2'}
        r3 = {'property':
              'hotsos.core.plugins.openstack.OpenstackChecksBase.r3'}
        requires = {'requires': [{'or': [r1, r2]}]}

        mock_plugin.return_value.r1 = False
        mock_plugin.return_value.r2 = False
        group = YDefsSection('test', requires)

        results = []
        for leaf in group.leaf_sections:
            self.assertEqual(len(leaf.requires), 1)
            for _requires in leaf.requires:
                for op in _requires:
                    for item in op:
                        for rtype in item:
                            for entry in rtype:
                                results.append(entry())

            self.assertFalse(leaf.requires.passes)

        self.assertFalse(group.leaf_sections[0].requires.passes)
        self.assertEqual(len(results), 2)
        self.assertEqual(results, [False, False])

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = False
        group = YDefsSection('test', requires)
        self.assertTrue(group.leaf_sections[0].requires.passes)

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = True
        group = YDefsSection('test', requires)
        self.assertTrue(group.leaf_sections[0].requires.passes)

        requires = {'requires': [{'and': [r1, r2]}]}

        mock_plugin.return_value.r1 = False
        mock_plugin.return_value.r2 = False
        group = YDefsSection('test', requires)
        self.assertFalse(group.leaf_sections[0].requires.passes)

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = False
        group = YDefsSection('test', requires)
        self.assertFalse(group.leaf_sections[0].requires.passes)

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = True
        group = YDefsSection('test', requires)
        self.assertTrue(group.leaf_sections[0].requires.passes)

        requires = {'requires': [{'and': [r1, r2],
                                  'or': [r1, r2]}]}

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = False
        group = YDefsSection('test', requires)
        self.assertFalse(group.leaf_sections[0].requires.passes)

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = True
        group = YDefsSection('test', requires)
        self.assertTrue(group.leaf_sections[0].requires.passes)

        requires = {'requires': [{'and': [r1, r2],
                                  'or': [r1, r2]}]}

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = False
        group = YDefsSection('test', requires)
        self.assertFalse(group.leaf_sections[0].requires.passes)

        requires = {'requires': [r1, {'and': [r3],
                                      'or': [r1, r2]}]}

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = False
        mock_plugin.return_value.r3 = True
        group = YDefsSection('test', requires)
        self.assertTrue(group.leaf_sections[0].requires.passes)

        requires = {'requires': [{'and': [r3],
                                  'or': [r1, r2]}]}

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = False
        mock_plugin.return_value.r3 = True
        group = YDefsSection('test', requires)
        self.assertTrue(group.leaf_sections[0].requires.passes)

        # same as prev test but with dict instead list
        requires = {'requires': {'and': [r3],
                                 'or': [r1, r2]}}

        mock_plugin.return_value.r1 = True
        mock_plugin.return_value.r2 = False
        mock_plugin.return_value.r3 = True
        group = YDefsSection('test', requires)
        self.assertTrue(group.leaf_sections[0].requires.passes)

    @mock.patch('hotsos.core.ycheck.engine.properties.requires.types.apt.'
                'APTPackageHelper')
    def test_yaml_def_requires_apt(self, mock_apt):
        tested = 0
        expected = {'2.0': False,
                    '3.0': True,
                    '3.1': True,
                    '4.0': True,
                    '5.0': True,
                    '5.2': True,
                    '5.3': False,
                    '6.0': False}
        mock_apt.return_value = mock.MagicMock()
        mock_apt.return_value.is_installed.return_value = True
        for ver, result in expected.items():
            mock_apt.return_value.get_version.return_value = ver
            mydef = YDefsSection('mydef',
                                 yaml.safe_load(YAML_DEF_REQUIRES_APT))
            for entry in mydef.leaf_sections:
                tested += 1
                self.assertEqual(entry.requires.passes, result)

        self.assertEqual(tested, len(expected))

    def test_yaml_def_requires_pebble_fail(self):
        mydef = YDefsSection('mydef',
                             yaml.safe_load(YAML_DEF_REQUIRES_PEBBLE_FAIL))
        for entry in mydef.leaf_sections:
            self.assertFalse(entry.requires.passes)

    def test_yaml_def_requires_systemd_pass(self):
        mydef = YDefsSection('mydef',
                             yaml.safe_load(YAML_DEF_REQUIRES_SYSTEMD_PASS_1))
        for entry in mydef.leaf_sections:
            self.assertTrue(entry.requires.passes)

    def test_yaml_def_requires_systemd_fail(self):
        mydef = YDefsSection('mydef',
                             yaml.safe_load(YAML_DEF_REQUIRES_SYSTEMD_FAIL_1))
        for entry in mydef.leaf_sections:
            self.assertFalse(entry.requires.passes)

        mydef = YDefsSection('mydef',
                             yaml.safe_load(YAML_DEF_REQUIRES_SYSTEMD_FAIL_2))
        for entry in mydef.leaf_sections:
            self.assertFalse(entry.requires.passes)

    def test_yaml_def_requires_systemd_started_after_pass(self):
        current = datetime.datetime.now()
        with mock.patch('hotsos.core.host_helpers.systemd.SystemdService',
                        FakeServiceObjectManager({
                            'neutron-openvswitch-agent':
                                current,
                            'openvswitch-switch':
                                current + datetime.timedelta(seconds=120)})):
            content = yaml.safe_load(YAML_DEF_REQUIRES_SYSTEMD_STARTED_AFTER)
            mydef = YDefsSection('mydef', content)
            for entry in mydef.leaf_sections:
                self.assertTrue(entry.requires.passes)

    def test_yaml_def_requires_systemd_started_after_fail(self):
        current = datetime.datetime.now()
        with mock.patch('hotsos.core.host_helpers.systemd.SystemdService',
                        FakeServiceObjectManager({'neutron-openvswitch-agent':
                                                  current,
                                                  'openvswitch-switch':
                                                  current})):
            content = yaml.safe_load(YAML_DEF_REQUIRES_SYSTEMD_STARTED_AFTER)
            mydef = YDefsSection('mydef', content)
            for entry in mydef.leaf_sections:
                self.assertFalse(entry.requires.passes)

        with mock.patch('hotsos.core.host_helpers.systemd.SystemdService',
                        FakeServiceObjectManager({
                            'neutron-openvswitch-agent': current,
                            'openvswitch-switch':
                                current + datetime.timedelta(seconds=119)})):
            content = yaml.safe_load(YAML_DEF_REQUIRES_SYSTEMD_STARTED_AFTER)
            mydef = YDefsSection('mydef', content)
            for entry in mydef.leaf_sections:
                self.assertFalse(entry.requires.passes)

    def test_cache_resolver(self):
        self.assertFalse(PropertyCacheRefResolver.is_valid_cache_ref('foo'))
        for test in [('foo', 'first', 'foo'),
                     (['foo', 'second'], 'first', 'foo'),
                     (['1', '2'], 'comma_join', '1, 2'),
                     (['1', '2', '1'], 'unique_comma_join', '1, 2'),
                     ({'1': 'foo', '2': 'bar', '3': 'blah'},
                      'comma_join', '1, 2, 3'),
                     ({'1': 'foo', '2': 'bar', '3': 'blah'},
                      'len', 3)]:
            out = PropertyCacheRefResolver.apply_renderer(test[0], test[1])
            self.assertEqual(out, test[2])
