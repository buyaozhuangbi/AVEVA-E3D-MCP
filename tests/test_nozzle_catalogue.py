"""Conservative two-cylinder catalogue reading, identity and change checks."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aveva_e3d_mcp.nozzle_catalogue import read_nozzle_catalogue_profiles
from aveva_e3d_mcp.nozzle_orientation_drawing import build_nozzle_orientation_drawing
from test_nozzle_axis_sheet import equipment_pose


def attributes(ref, kind, name, **values):
    return {'success': True, 'refno': ref, 'type': kind,
            'values': {'NAME': name, 'TYPE': kind, **values}, 'errors': {},
            'typed_values': {'NAME': name}, 'typed_read_errors': {}}


def member(ref, kind, name, index):
    return {'refno': ref, 'type': kind, 'name': name, 'sibling_index': index}


def members(ref, kind, name, children=None):
    children = children or []
    return {'success': True, 'parent': {'refno': ref, 'type': kind, 'name': name}, 'members': children,
            'returned_count': len(children), 'limit': 100, 'has_more': False,
            'next_cursor': None, 'read_errors': [], 'consistency': 'live'}


def drawing_fixture(count=1):
    pose = equipment_pose()
    records = []
    for index in range(count):
        points = []
        for number, z, direction in ((1, 100, [0, 0, 1]), (2, 0, [0, 0, -1])):
            points.append({'number': number, 'complete': True,
                'position': {'available': True, 'unit': 'mm', 'x': 1200 + 300 * index, 'y': 2000, 'z': 3000 + z},
                'direction': {'available': True, **dict(zip(('x', 'y', 'z'), direction))}})
        records.append({'refno': f'/V-100/N{index + 1}', 'native': {'ok': True, 'version': 'E3D21', 'result': {
            'success': True, 'complete': True, 'frame': deepcopy(pose['native_frame']),
            'element': {'refno': f'=1/{200 + index}', 'type': 'NOZZ', 'name': f'/V-100/N{index + 1}'}, 'points': points}}})
    drawing, _ = build_nozzle_orientation_drawing(records, width=320, height=240)
    return drawing, pose


class CatalogueBridge:
    def __init__(self, count=1, *, negative_children=True):
        self.attrs, self.lists, self.calls = {}, {}, []
        self.mutate = None
        for index in range(count):
            ref, name = f'=1/{200 + index}', f'/V-100/N{index + 1}'
            self.attrs[ref] = attributes(ref, 'NOZZ', name, OWNER='/V-100', CATR='/SPEC/NOZZ',
                GMRE='/NOZZ-G', PTRE='/NOZZ-P', PARA='80 FBD 210 29 89 ', HEIG='100mm', IPAR='unset')
            self.attrs[ref]['typed_values']['HEIG'] = 100.0
        self.attrs['=2/10'] = attributes('=2/10', 'SPCO', '/SPEC/NOZZ', CATR='/NOZZ-S',
                                      GMRE='/NOZZ-G', PTRE='/NOZZ-P', PARA='80 FBD 210 29 89 ')
        self.attrs['=2/11'] = attributes('=2/11', 'SCOM', '/NOZZ-S', CATR='/NOZZ-S',
                                      GMRE='/NOZZ-G', PTRE='/NOZZ-P', PARA='80 FBD 210 29 89 ')
        self.lists['=2/20'] = members('=2/20', 'GMSE', '/NOZZ-G', [
            member('=2/21', 'SCYL', '=2/21', 1), member('=2/23', 'SCYL', '=2/23', 2), member('=2/25', 'LINE', '=2/25', 3)])
        self.lists['=2/30'] = members('=2/30', 'PTSE', '/NOZZ-P', [
            member('=2/31', 'PTAX', '=2/31', 1), member('=2/32', 'PTAX', '=2/32', 2)])
        for i, ref, diameter, height in ((1, '=2/21', 'SUM PARAM 3 IPARAM 1', 'PARAM 4'),
                                       (2, '=2/23', 'SUM PARAM 5 IPARAM 1', 'DDHEIGHT')):
            self.attrs[ref] = attributes(ref, 'SCYL', ref, OWNER='/NOZZ-G', PAXI='X', PDIS='0', PDIA=diameter, PHEI=height)
            child_ref = f'=2/{22 if i == 1 else 24}'
            self.lists[ref] = members(ref, 'SCYL', ref, [member(child_ref, 'NSCY', child_ref, 1)] if negative_children else [])
            if negative_children:
                self.attrs[child_ref] = attributes(child_ref, 'NSCY', child_ref,
                    OWNER=f'SCYLINDER {i} of GMSET /NOZZ-G', PAXI='X', PDIS='0', PDIA='PARAM 1', PHEI=height)
                self.lists[child_ref] = members(child_ref, 'NSCY', child_ref)
        self.attrs['=2/31'] = attributes('=2/31', 'PTAX', '=2/31', OWNER='/NOZZ-P', NUMB='1', PAXI='-X', PDIS='0', PBOR='PARAM 1')
        self.attrs['=2/32'] = attributes('=2/32', 'PTAX', '=2/32', OWNER='/NOZZ-P', NUMB='2', PAXI='X', PDIS='DDHEIGHT', PBOR='0')
        self.rounds = 0

    def resolve(self, ref, table):
        if ref in table:
            return ref
        for key, value in table.items():
            name = value.get('values', {}).get('NAME') or value.get('typed_values', {}).get('NAME') or value.get('parent', {}).get('name')
            if ref == name:
                return key
        raise AssertionError('Unexpected reference: ' + ref)

    def read_attributes(self, ref, names):
        if ref == '=1/200':
            self.rounds += 1
        key = self.resolve(ref, self.attrs)
        result = deepcopy(self.attrs[key])
        result['values'] = {field: value for field, value in result['values'].items() if field in names}
        result['typed_values'] = {field: value for field, value in result['typed_values'].items() if field in names}
        return self.reply('attributes', key, ref, names, result)

    def list_members(self, ref, cursor, limit):
        if cursor != '' or limit != 100:
            raise AssertionError('Unbounded or continued native read')
        key = self.resolve(ref, self.lists)
        return self.reply('members', key, ref, None, deepcopy(self.lists[key]))

    def reply(self, operation, key, requested, names, result):
        self.calls.append((self.rounds, operation, key, requested, deepcopy(names)))
        reply = {'ok': True, 'version': 'E3D21', 'result': result}
        if self.mutate:
            self.mutate(self.rounds, operation, key, reply)
        return reply


class NozzleCatalogueTests(unittest.TestCase):
    def read(self, bridge=None, drawing=None, pose=None):
        default_drawing, default_pose = drawing_fixture()
        bridge = bridge or CatalogueBridge()
        drawing, pose = (default_drawing if drawing is None else drawing), (default_pose if pose is None else pose)
        before = deepcopy((drawing, pose, bridge.attrs, bridge.lists))
        result = read_nozzle_catalogue_profiles(bridge, drawing, pose, version='E3D21')
        self.assertEqual((drawing, pose, bridge.attrs, bridge.lists), before)
        return result

    def assert_failed(self, result, pattern=None):
        self.assertIs(result['success'], False)
        self.assertIs(result['complete'], False)
        self.assertEqual(result['profiles'], [])
        self.assertIs(result['evidence']['success'], False)
        if pattern:
            self.assertIn(pattern, result['evidence']['error_code'])

    def test_real_expression_mapping_returns_bare_OD_and_native_length(self):
        result = self.read()
        self.assertTrue(result['success'], result)
        profile, = result['profiles']
        self.assertEqual(profile['stations'], [
            {'distance_mm': 0, 'outer_diameter_mm': 89}, {'distance_mm': 71, 'outer_diameter_mm': 89},
            {'distance_mm': 71, 'outer_diameter_mm': 210}, {'distance_mm': 100, 'outer_diameter_mm': 210}])
        self.assertEqual(profile['axis_start_point_number'], 2)
        self.assertEqual(profile['axis_end_point_number'], 1)
        self.assertEqual(profile['nominal_bore_mm'], 80)
        proof = profile['dimension_evidence']
        self.assertTrue(proof['bare_catalogue_only'])
        self.assertFalse(proof['insulation_applied'])
        self.assertFalse(proof['bore_geometry_inferred'])
        self.assertEqual(len(proof['verified_coaxial_bore_definitions']), 2)
        self.assertTrue(all(row['bore_surface_drawn'] is False and row['affects_outside_envelope'] is False
                            for row in proof['verified_coaxial_bore_definitions']))
        evidence = result['evidence']
        self.assertEqual(len(evidence['observations']), 2)
        self.assertEqual(evidence['observations'][0], evidence['observations'][1])
        self.assertTrue(evidence['matching_observations'])
        expected = hashlib.sha256(json.dumps(evidence['observations'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.assertEqual(evidence['proof_sha256'], expected)
        self.assertFalse(evidence['atomic_snapshot'])

    def test_three_nozzles_share_catalogue_reads_within_each_of_two_independent_observations(self):
        drawing, pose = drawing_fixture(3)
        bridge = CatalogueBridge(3)
        result = self.read(bridge, drawing, pose)
        self.assertTrue(result['success'], result)
        self.assertEqual([profile['resolved_refno'] for profile in result['profiles']], ['=1/200', '=1/201', '=1/202'])
        self.assertEqual([row for row in bridge.calls if row[1:3] == ('attributes', '=2/11')].__len__(), 2)
        self.assertEqual({row[0] for row in bridge.calls}, {1, 2})

    def test_no_negative_children_remains_supported(self):
        self.assertTrue(self.read(CatalogueBridge(negative_children=False))['success'])

    def test_missing_any_requested_attribute_is_refused(self):
        for ref, field in (('=1/200', 'OWNER'), ('=1/200', 'IPAR'), ('=1/200', 'HEIG'), ('=2/10', 'GMRE'),
                           ('=2/11', 'NAME'), ('=2/21', 'PDIA'), ('=2/22', 'PHEI'), ('=2/31', 'NUMB')):
            bridge = CatalogueBridge()
            bridge.attrs[ref]['values'].pop(field)
            with self.subTest(ref=ref, field=field):
                self.assert_failed(self.read(bridge), 'requested_attributes_missing')

    def test_receipt_false_partial_errors_or_wrong_version_are_refused(self):
        mutations = [lambda r: r.update(ok=False), lambda r: r.update(version='E3D22'),
                     lambda r: r['result'].update(success=1), lambda r: r['result'].update(complete=False),
                     lambda r: r['result'].update(available=False), lambda r: r['result'].update(partial=True),
                     lambda r: r['result'].update(errors={'PDIA': 'failed'}),
                     lambda r: r['result'].update(typed_read_errors={'HEIG': 'failed'})]
        for index, mutate in enumerate(mutations):
            bridge = CatalogueBridge()
            bridge.mutate = lambda _, op, ref, reply: mutate(reply) if op == 'attributes' and ref == '=1/200' else None
            with self.subTest(index=index):
                self.assert_failed(self.read(bridge), 'complete_read_required')

    def test_attribute_identity_type_name_and_typed_name_must_agree(self):
        mutations = [lambda r: r.update(refno='=9/9'), lambda r: r.update(type='BOX'),
                     lambda r: r['values'].update(TYPE='BOX'), lambda r: r['values'].update(NAME='/WRONG'),
                     lambda r: r.update(name='/WRONG'), lambda r: r['typed_values'].update(NAME='/WRONG')]
        for index, mutate in enumerate(mutations):
            bridge = CatalogueBridge()
            mutate(bridge.attrs['=2/21'])
            with self.subTest(index=index):
                self.assert_failed(self.read(bridge))

    def test_wrong_direct_equipment_owner_and_nozzle_name_are_refused(self):
        for field, value in (('OWNER', '/OTHER'), ('OWNER', '=1/999'), ('NAME', '/V-100/N-OTHER')):
            bridge = CatalogueBridge()
            bridge.attrs['=1/200']['values'][field] = value
            bridge.attrs['=1/200']['typed_values']['NAME'] = bridge.attrs['=1/200']['values']['NAME']
            with self.subTest(field=field):
                self.assert_failed(self.read(bridge))

    def test_changed_second_attribute_observation_suppresses_all_profiles(self):
        bridge = CatalogueBridge()
        bridge.mutate = lambda turn, op, ref, reply: reply['result']['values'].update(PARA='80 FBD 210 29 89  ') \
            if turn == 2 and op == 'attributes' and ref == '=1/200' else None
        self.assert_failed(self.read(bridge))

    def test_changed_second_member_observation_suppresses_all_profiles(self):
        bridge = CatalogueBridge()
        bridge.mutate = lambda turn, op, ref, reply: reply['result'].update(consistency='changed') \
            if turn == 2 and op == 'members' and ref == '=2/20' else None
        self.assert_failed(self.read(bridge), 'observations_changed')

    def test_incomplete_member_count_pagination_parent_and_duplicate_members_are_refused(self):
        mutations = [lambda r: r.update(returned_count=2), lambda r: r.update(returned_count=True),
                     lambda r: r.update(has_more=True), lambda r: r.update(next_cursor='more'),
                     lambda r: r.update(read_errors=['failed']), lambda r: r.pop('read_errors'),
                     lambda r: r['parent'].update(name='/OTHER'), lambda r: r['parent'].update(type='SCYL'),
                     lambda r: r['members'][1].update(refno='=2/21'),
                     lambda r: r['members'][0].update(sibling_index=True),
                     lambda r: r['members'][0].update(name='/OTHER')]
        for index, mutate in enumerate(mutations):
            bridge = CatalogueBridge()
            mutate(bridge.lists['=2/20'])
            with self.subTest(index=index):
                self.assert_failed(self.read(bridge))

    def test_spco_scom_and_design_parameters_and_refs_must_match(self):
        for ref, field, value in (('=2/10', 'PARA', '80 FBD 211 29 89 '), ('=2/11', 'PARA', '80 FBD 211 29 89 '),
                                  ('=2/10', 'GMRE', '/OTHER'), ('=2/11', 'CATR', '/OTHER'), ('=2/11', 'PTRE', '/OTHER')):
            bridge = CatalogueBridge()
            bridge.attrs[ref]['values'][field] = value
            with self.subTest(ref=ref, field=field):
                self.assert_failed(self.read(bridge), 'design_catalogue_mismatch')

    def test_unsupported_or_unsafe_expression_is_never_evaluated(self):
        for expression in ('SUM PARAM 3 IPARAM 2', 'PARAM 3', 'EXEC SOMETHING', '__import__("os")', 'SUM PARAM 3 IPARAM 1 + 1'):
            bridge = CatalogueBridge()
            bridge.attrs['=2/21']['values']['PDIA'] = expression
            with self.subTest(expression=expression):
                self.assert_failed(self.read(bridge), 'geometry_expression_unsupported')

    def test_unrecognized_gmse_positive_solid_is_refused(self):
        bridge = CatalogueBridge()
        bridge.lists['=2/20']['members'][2]['type'] = 'SBOX'
        self.assert_failed(self.read(bridge), 'geometry_unsupported')

    def test_only_exact_coaxial_param1_bore_and_matching_owner_are_allowed(self):
        for field, value in (('PAXI', 'Y'), ('PDIA', 'SUM PARAM 1 10'), ('PDIS', '1'), ('PHEI', 'DDHEIGHT'),
                             ('OWNER', 'SCYLINDER 2 of GMSET /NOZZ-G')):
            bridge = CatalogueBridge()
            bridge.attrs['=2/22']['values'][field] = value
            with self.subTest(field=field):
                self.assert_failed(self.read(bridge), 'bore_')

    def test_negative_child_other_type_multiple_children_or_grandchildren_are_refused(self):
        for mutation in ('type', 'many', 'grandchild'):
            bridge = CatalogueBridge()
            if mutation == 'type':
                bridge.lists['=2/21']['members'][0]['type'] = 'NSBO'
            elif mutation == 'many':
                bridge.lists['=2/21']['members'].append(member('=2/26', 'NSCY', '=2/26', 2))
                bridge.lists['=2/21']['returned_count'] = 2
            else:
                bridge.lists['=2/22']['members'].append(member('=2/26', 'NSCY', '=2/26', 1))
                bridge.lists['=2/22']['returned_count'] = 1
            with self.subTest(mutation=mutation):
                self.assert_failed(self.read(bridge))

    def test_nominal_bore_is_not_OD_and_must_fit_inside_bare_neck(self):
        for parameters in ('89 FBD 210 29 89 ', '100 FBD 210 29 89 ', '80 FBD 80 29 89 ',
                           '80 FBD 210 100 89 ', '80 FBD 210 0 89 ', '80 OTHER 210 29 89 ',
                           '80 FBD 210 29 89 5 ', '80 FBD 1e999 29 89 '):
            bridge = CatalogueBridge()
            for ref in ('=1/200', '=2/10', '=2/11'):
                bridge.attrs[ref]['values']['PARA'] = parameters
            with self.subTest(parameters=parameters):
                self.assert_failed(self.read(bridge))

    def test_nonunset_insulation_parameters_are_refused_instead_of_added_or_assumed_zero(self):
        for value in ('0', '10', '0 0', 'unknown'):
            bridge = CatalogueBridge()
            bridge.attrs['=1/200']['values']['IPAR'] = value
            with self.subTest(value=value):
                self.assert_failed(self.read(bridge), 'insulation_parameters_unsupported')

    def test_native_height_typed_text_and_ppos_separation_must_agree(self):
        for value in (False, -1, 0, 1e7, math.nan, math.inf, 99):
            bridge = CatalogueBridge()
            bridge.attrs['=1/200']['typed_values']['HEIG'] = value
            with self.subTest(value=value):
                self.assert_failed(self.read(bridge), 'invalid_profile_dimensions')
        drawing, pose = drawing_fixture()
        drawing['nozzles'][0]['position']['z'] += 1
        self.assert_failed(self.read(drawing=drawing, pose=pose), 'axis_length_mismatch')

    def test_ppos_direction_orientation_must_match_the_two_point_order(self):
        for field, value in (('z', -1), ('x', .1), ('available', False)):
            drawing, pose = drawing_fixture()
            drawing['nozzles'][0]['direction'][field] = value
            with self.subTest(field=field):
                self.assert_failed(self.read(drawing=drawing, pose=pose))

    def test_typed_expression_and_point_number_cannot_contradict_native_text(self):
        for ref, field, value in (('=2/21', 'PAXI', 'Y'), ('=2/22', 'PDIA', 'PARAM 9'),
                                  ('=2/31', 'NUMB', True), ('=2/31', 'NUMB', 2)):
            bridge = CatalogueBridge()
            bridge.attrs[ref]['typed_values'][field] = value
            with self.subTest(ref=ref, field=field):
                self.assert_failed(self.read(bridge), 'typed_')

    def test_points_must_be_complete_distinct_p1_p2_in_same_native_world(self):
        mutations = [lambda d: d.update(complete=False), lambda d: d['nozzles'].pop(),
                     lambda d: d['nozzles'][0].update(point_number=True),
                     lambda d: d['nozzles'][1].update(point_number=1),
                     lambda d: d['nozzles'][0].update(resolved_refno='=9/9'),
                     lambda d: d['nozzles'][0]['position'].update(unit='m'),
                     lambda d: d['nozzles'][0]['native_frame'].update(refno='=2/0')]
        for index, mutate in enumerate(mutations):
            drawing, pose = drawing_fixture()
            mutate(drawing)
            with self.subTest(index=index):
                self.assert_failed(self.read(drawing=drawing, pose=pose))


if __name__ == '__main__':
    unittest.main()
