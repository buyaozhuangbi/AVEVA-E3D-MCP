"""Read a bounded, explicit coaxial SCYL nozzle catalogue without evaluating PML.

Only the witnessed two-cylinder flange/neck and two-PTAX convention is mapped.
Unknown expressions, extra solids, changed observations and incomplete ownership
fail explicitly. Nominal bore is retained as a schedule value, never used as OD.
"""
from __future__ import annotations
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import re

from .nozzle_axis_sheet import _equipment_frame, _prepare_axis_rows, _reference_key

_REF = re.compile(r"=\d+/\d+$")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
_DESIGN = ['NAME','TYPE','OWNER','CATR','GMRE','PTRE','HEIG','PARA','IPAR']
_CATALOGUE = ['NAME','TYPE','CATR','GMRE','PTRE','PARA']
_CYLINDER = ['NAME','TYPE','OWNER','PAXI','PDIA','PDIS','PHEI']
_POINT = ['NAME','TYPE','OWNER','PAXI','PDIS','PBOR','NUMB']
_LENGTH = re.compile(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s*mm$', re.IGNORECASE)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _numeric(value):
    if not isinstance(value, str) or not _NUMBER.fullmatch(value.strip()):
        raise ValueError('nozzle_catalogue_numeric_parameter_required')
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > 1_000_000:
        raise ValueError('nozzle_catalogue_dimension_out_of_range')
    return number


def _matches(reference, identity):
    try:
        key = _reference_key(reference)
        return key == _reference_key(identity['refno'], numeric=True) if key.startswith('=') else key == _reference_key(identity['name'])
    except (ValueError, KeyError, TypeError):
        return False


def _identity(value):
    if not isinstance(value, Mapping):
        raise ValueError('nozzle_catalogue_native_identity_required')
    _reference_key(value.get('refno'), numeric=True)
    _reference_key(value.get('name'))
    if value['name'].startswith('=') and _reference_key(value['name'], numeric=True) != _reference_key(value['refno'], numeric=True):
        raise ValueError('nozzle_catalogue_native_name_identity_mismatch')
    if not isinstance(value.get('type'), str) or not value['type']:
        raise ValueError('nozzle_catalogue_native_type_required')
    return value


def _attribute_identity(result, reference, attributes):
    values = result.get('values')
    if (not isinstance(values, Mapping) or any(not isinstance(values.get(field), str) or not values[field].strip()
                                               for field in attributes)):
        raise ValueError('nozzle_catalogue_requested_attributes_missing')
    identity = _identity({'refno': result.get('refno'), 'name': values.get('NAME'), 'type': result.get('type')})
    if not _matches(reference, identity) or values['TYPE'] != result['type']:
        raise ValueError('nozzle_catalogue_attribute_identity_mismatch')
    if 'name' in result and result['name'] != values['NAME']:
        raise ValueError('nozzle_catalogue_attribute_name_mismatch')
    typed = result.get('typed_values', {})
    if not isinstance(typed, Mapping) or ('NAME' in typed and typed['NAME'] != values['NAME']):
        raise ValueError('nozzle_catalogue_typed_identity_mismatch')
    for field in ('PAXI','PDIS','PDIA','PHEI','PBOR'):
        if field in typed and (not isinstance(typed[field], str) or
                              ' '.join(typed[field].upper().split()) != ' '.join(values[field].upper().split())):
            raise ValueError('nozzle_catalogue_typed_expression_mismatch')
    return identity


def _member_identity(result, reference):
    parent = _identity(result.get('parent'))
    members = result.get('members')
    if not _matches(reference, parent):
        raise ValueError('nozzle_catalogue_member_parent_mismatch')
    if (not isinstance(members, list) or len(members) > 100 or type(result.get('returned_count')) is not int
            or result['returned_count'] != len(members) or result.get('has_more') is not False
            or result.get('next_cursor') is not None or result.get('limit') != 100
            or result.get('read_errors') != []):
        raise ValueError('nozzle_catalogue_complete_member_list_required')
    seen = {_reference_key(parent['refno'], numeric=True)}
    for index, member in enumerate(members, 1):
        _identity(member)
        key = _reference_key(member['refno'], numeric=True)
        if key in seen or type(member.get('sibling_index')) is not int or member['sibling_index'] != index:
            raise ValueError('nozzle_catalogue_member_identity_invalid')
        seen.add(key)
    return parent


def _same_identity(attributes, member):
    if (attributes['refno'] != member['refno'] or attributes['type'] != member['type']
            or attributes['values']['NAME'] != member['name']):
        raise ValueError('nozzle_catalogue_member_attribute_mismatch')


def _expression(values, fields):
    return tuple(' '.join(values[field].upper().split()) for field in fields)


def _position(row):
    value = row.get('position')
    if not isinstance(value, Mapping) or value.get('unit') != 'mm' or value.get('available') is not True:
        raise ValueError('nozzle_catalogue_native_point_required')
    point = [value.get(axis) for axis in ('x','y','z')]
    if any(type(x) not in {int,float} or not math.isfinite(x) for x in point):
        raise ValueError('nozzle_catalogue_native_point_required')
    return point


def read_nozzle_catalogue_profiles(bridge, drawing, equipment_pose, *, version):
    """Return profiles plus original reads; caller checks session/project boundaries."""
    evidence = {'observations': [], 'atomic_snapshot': False, 'read_only': True,
        'geometry_scope': 'bare catalogue outside envelope; no insulation, bolt holes, bore surface or wall inferred',
        'supported_mapping': 'two coaxial SCYL and two PTAX; optional verified coaxial PARAM 1 bore NSCY; direct EQUI ownership'}
    try:
        frame = _equipment_frame(equipment_pose, 'Z', 'X')
        native_rows = _prepare_axis_rows(drawing, frame)
        groups = {}
        for row in native_rows:
            ref = row.get('resolved_refno')
            if not isinstance(ref, str) or not _REF.fullmatch(ref):
                raise ValueError('nozzle_catalogue_native_identity_required')
            groups.setdefault(ref, []).append(row)
        if not 1 <= len(groups) <= 32:
            raise ValueError('nozzle_catalogue_target_limit')
        observations = []
        for observation in range(2):
            retained, cache = [], {}
            def request(kind, ref, attributes=None):
                key = (kind,ref,tuple(attributes or []))
                if key in cache:
                    return cache[key]
                receipt = (bridge.read_attributes(ref,attributes) if kind == 'attributes'
                           else bridge.list_members(ref,'',100))
                retained.append({'operation':kind,'refno':ref,'attributes':attributes,'native':deepcopy(receipt)})
                result = receipt.get('result') if isinstance(receipt,Mapping) else None
                if (not isinstance(result,Mapping) or receipt.get('ok') is not True or receipt.get('version') != version
                        or result.get('success') is not True or result.get('errors') or result.get('read_errors')
                        or result.get('typed_read_errors') or result.get('complete', True) is not True
                        or result.get('available', True) is not True or result.get('partial', False) is not False):
                    raise ValueError('nozzle_catalogue_complete_read_required')
                if kind == 'members' and (result.get('has_more') is not False or result.get('next_cursor') is not None):
                    raise ValueError('nozzle_catalogue_member_limit')
                if kind == 'attributes':
                    _attribute_identity(result, ref, attributes)
                else:
                    _member_identity(result, ref)
                cache[key] = result
                return result
            profiles = []
            evidence['observations'].append(retained)
            for ref,rows in groups.items():
                by_point = {row.get('point_number'):row for row in rows}
                if len(by_point) != len(rows) or set(by_point) != {1,2} or any(type(row.get('point_number')) is not int for row in rows):
                    raise ValueError('nozzle_catalogue_requires_native_p1_p2')
                design = request('attributes',ref,_DESIGN)
                values = design['values']
                if design['type'] != 'NOZZ' or design['refno'] != ref:
                    raise ValueError('nozzle_catalogue_type_mismatch')
                if any(row.get('element_name') != values['NAME'] for row in rows):
                    raise ValueError('nozzle_catalogue_point_design_name_mismatch')
                if not _matches(values['OWNER'], {'refno':equipment_pose['resolved_refno'], 'name':equipment_pose['element_name']}):
                    raise ValueError('nozzle_catalogue_direct_equipment_owner_required')
                if values['IPAR'].strip().casefold() != 'unset':
                    raise ValueError('nozzle_catalogue_insulation_parameters_unsupported')
                scom = request('attributes',values['CATR'],_CATALOGUE)
                catalogue_chain = [scom]
                if scom['type'] == 'SPCO':
                    scom = request('attributes',scom['values']['CATR'],_CATALOGUE)
                    catalogue_chain.append(scom)
                if scom['type'] != 'SCOM':
                    raise ValueError('nozzle_catalogue_scom_required')
                cv = scom['values']
                if (not _matches(cv['CATR'], {'refno':scom['refno'], 'name':cv['NAME']})
                        or any(values[field] != entry['values'][field] for entry in catalogue_chain for field in ('GMRE','PTRE','PARA'))):
                    raise ValueError('nozzle_catalogue_design_catalogue_mismatch')
                parameters = values['PARA'].split()
                if len(parameters) != 5 or parameters[1] != 'FBD':
                    raise ValueError('nozzle_catalogue_parameter_layout_unsupported')
                bore,flange_od,thickness,neck_od = (_numeric(parameters[i]) for i in (0,2,3,4))
                height = design.get('typed_values',{}).get('HEIG')
                text_height = _LENGTH.fullmatch(values['HEIG'].strip())
                if (type(height) not in {int,float} or not math.isfinite(height) or not 0 < thickness < height <= 1_000_000
                        or not bore < neck_od < flange_od or text_height is None
                        or not math.isclose(float(text_height[1]), height, rel_tol=1e-12, abs_tol=1e-9)):
                    raise ValueError('nozzle_catalogue_invalid_profile_dimensions')
                members = request('members',values['GMRE'])
                if members.get('parent',{}).get('type') != 'GMSE':
                    raise ValueError('nozzle_catalogue_gmse_required')
                solids = [member for member in members['members'] if member['type'] == 'SCYL']
                if len(solids) != 2 or any(member['type'] not in {'SCYL','LINE'} for member in members['members']):
                    raise ValueError('nozzle_catalogue_geometry_unsupported')
                expressions = []
                bore_definitions = []
                for solid in solids:
                    native = request('attributes',solid['refno'],_CYLINDER)
                    _same_identity(native, solid)
                    v = native['values']
                    if native['type'] != 'SCYL' or v['OWNER'] not in {values['GMRE'],members['parent']['refno']}:
                        raise ValueError('nozzle_catalogue_geometry_owner_mismatch')
                    expressions.append(_expression(v, ('PAXI','PDIS','PDIA','PHEI')))
                    child_list = request('members',solid['refno'])
                    if child_list['parent'] != {key:solid[key] for key in ('refno','type','name')}:
                        raise ValueError('nozzle_catalogue_solid_member_parent_mismatch')
                    children = child_list['members']
                    if len(children) > 1 or any(child['type'] != 'NSCY' for child in children):
                        raise ValueError('nozzle_catalogue_negative_children_unsupported')
                    for child in children:
                        negative = request('attributes', child['refno'], _CYLINDER)
                        _same_identity(negative, child)
                        nv = negative['values']
                        negative_owner = f"SCYLINDER {solid['sibling_index']} of GMSET {members['parent']['name']}"
                        if nv['OWNER'] not in {solid['refno'], negative_owner}:
                            raise ValueError('nozzle_catalogue_bore_owner_mismatch')
                        if _expression(nv, ('PAXI','PDIS','PDIA','PHEI')) != ('X','0','PARAM 1',expressions[-1][3]):
                            raise ValueError('nozzle_catalogue_bore_expression_unsupported')
                        descendants = request('members', child['refno'])
                        if descendants['parent'] != {key:child[key] for key in ('refno','type','name')} or descendants['members']:
                            raise ValueError('nozzle_catalogue_bore_descendants_unsupported')
                        bore_definitions.append({'refno':child['refno'],'owner_refno':solid['refno'],
                            'expressions':_expression(nv, ('PAXI','PDIS','PDIA','PHEI')),
                            'nominal_bore_mm':bore,'affects_outside_envelope':False,'bore_surface_drawn':False})
                if sorted(expressions) != sorted([('X','0','SUM PARAM 3 IPARAM 1','PARAM 4'),
                                                    ('X','0','SUM PARAM 5 IPARAM 1','DDHEIGHT')]):
                    raise ValueError('nozzle_catalogue_geometry_expression_unsupported')
                point_set = request('members',values['PTRE'])
                if point_set.get('parent',{}).get('type') != 'PTSE' or len(point_set['members']) != 2:
                    raise ValueError('nozzle_catalogue_point_definition_unsupported')
                axes = []
                for point in point_set['members']:
                    if point['type'] != 'PTAX':
                        raise ValueError('nozzle_catalogue_point_definition_unsupported')
                    native = request('attributes',point['refno'],_POINT)
                    _same_identity(native, point)
                    v = native['values']
                    if native['type'] != 'PTAX' or v['OWNER'] not in {values['PTRE'],point_set['parent']['refno']}:
                        raise ValueError('nozzle_catalogue_point_owner_mismatch')
                    axes.append(_expression(v, ('NUMB','PAXI','PDIS','PBOR')))
                    if 'NUMB' in native.get('typed_values',{}) and (type(native['typed_values']['NUMB']) is not int
                            or native['typed_values']['NUMB'] != int(v['NUMB'])):
                        raise ValueError('nozzle_catalogue_typed_point_number_mismatch')
                if sorted(axes) != [('1','-X','0','PARAM 1'),('2','X','DDHEIGHT','0')]:
                    raise ValueError('nozzle_catalogue_point_expression_unsupported')
                start,end = _position(by_point[2]),_position(by_point[1])
                length = math.dist(start,end)
                if not math.isclose(length,height,rel_tol=1e-9,abs_tol=1e-6):
                    raise ValueError('nozzle_catalogue_axis_length_mismatch')
                for number,sign in ((1,1),(2,-1)):
                    direction = by_point[number].get('direction',{})
                    if direction.get('available') is not True or any(not math.isclose(direction.get(axis,float('nan')),
                            sign*(end[i]-start[i])/length,abs_tol=1e-6) for i,axis in enumerate(('x','y','z'))):
                        raise ValueError('nozzle_catalogue_axis_direction_mismatch')
                profiles.append({'resolved_refno':ref,'axis_start_point_number':2,'axis_end_point_number':1,
                    'stations':[{'distance_mm':0.0,'outer_diameter_mm':neck_od},
                        {'distance_mm':height-thickness,'outer_diameter_mm':neck_od},
                        {'distance_mm':height-thickness,'outer_diameter_mm':flange_od},
                        {'distance_mm':height,'outer_diameter_mm':flange_od}],
                    'nominal_bore_mm':bore,'dimension_evidence':{
                        'source':'native SCOM parameters and GMSE SCYL/PTSE PTAX expression whitelist',
                        'design_refno':ref,'scom_refno':scom['refno'],'gmse_refno':members['parent']['refno'],
                        'ptse_refno':point_set['parent']['refno'],'parameters':parameters,'height_mm':height,
                        'expressions':expressions,'native_point_separation_mm':length,
                        'verified_coaxial_bore_definitions':bore_definitions,
                        'bare_catalogue_only':True,'insulation_applied':False,'bore_geometry_inferred':False}})
            observations.append(profiles)
        if _canonical(observations[0]) != _canonical(observations[1]) or _canonical(evidence['observations'][0]) != _canonical(evidence['observations'][1]):
            raise ValueError('nozzle_catalogue_observations_changed')
        evidence.update(success=True,complete=True,matching_observations=True,
                        proof_sha256=hashlib.sha256(_canonical(evidence['observations']).encode()).hexdigest())
        return {'success':True,'complete':True,'profiles':observations[0],'evidence':evidence}
    except Exception as exc:
        evidence.update(success=False,complete=False,error_code=str(exc)[:512],error_type=type(exc).__name__)
        return {'success':False,'complete':False,'profiles':[],'evidence':evidence}
