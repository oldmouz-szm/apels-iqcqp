"""Exact hypervolume for independently checked 2--4 objective LP results."""
from fractions import Fraction
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import time

from mo_iqcqp.archive import dominates
from mo_iqcqp.experiment.resources import input_limits
from mo_iqcqp.io import load_lp

SCHEMA = 'hv-v1'


def number(value):
    """Parse finite decimal/rational numbers without accepting booleans or NaN."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal, Fraction)):
        raise ValueError('Metric coordinates must be finite numbers')
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('Metric coordinates must be finite numbers') from exc
    return result


def vector(values, dimensions, label):
    if not isinstance(values, list) or len(values) != dimensions:
        raise ValueError(f'{label} must contain {dimensions} coordinates')
    return tuple(number(value) for value in values)


def original_to_internal(original, directions):
    if len(original) != len(directions) or any(direction not in ('min', 'max') for direction in directions):
        raise ValueError('Objective directions must match the point dimension')
    return tuple(value if direction == 'min' else -value
                 for value, direction in zip(original, directions))


def nondominated(points):
    """Exact set filtering; order and duplicate multiplicity do not alter metrics."""
    distinct = sorted(set(points))
    return tuple(point for point in distinct
                 if not any(dominates(other, point) for other in distinct if other != point))


def hypervolume(points, reference):
    """Exact union of [point, reference] boxes for 2--4 minimization objectives."""
    reference = tuple(map(number, reference))
    if not 2 <= len(reference) <= 4:
        raise ValueError('HV supports 2--4 objectives')
    points = [tuple(map(number, point)) for point in points]
    if any(len(point) != len(reference) for point in points):
        raise ValueError('HV point dimension mismatch')
    if any(any(x >= r for x, r in zip(point, reference)) for point in points):
        raise ValueError('HV reference must be strictly worse than every archived point')
    points = nondominated(points)

    def sweep(front, ref):
        if not front:
            return Fraction(0)
        if len(ref) == 1:
            return ref[0] - min(point[0] for point in front)
        if len(ref) == 2:
            # On the 2D skyline, x increases while y decreases. Each point
            # contributes a new horizontal strip; this avoids repeated sweeps.
            previous_y = ref[1]
            area = Fraction(0)
            for x, y in nondominated(front):
                if y < previous_y:
                    area += (ref[0] - x) * (previous_y - y)
                    previous_y = y
            return area
        # Every slab [x_i, x_(i+1)) is covered by the boxes whose first
        # coordinate is at most x_i. Recursing computes its projected union.
        coordinates = sorted({point[0] for point in front})
        active = []
        grouped = {}
        for point in front:
            grouped.setdefault(point[0], []).append(point[1:])
        total = Fraction(0)
        for index, coordinate in enumerate(coordinates):
            active.extend(grouped[coordinate])
            next_coordinate = coordinates[index + 1] if index + 1 < len(coordinates) else ref[0]
            total += (next_coordinate - coordinate) * sweep(nondominated(active), ref[1:])
        return total

    return sweep(points, reference)


def _read_json(path):
    data = Path(path).read_bytes()
    try:
        value = json.loads(data, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f'Invalid JSON: {path}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'Expected JSON object: {path}')
    return value, hashlib.sha256(data).hexdigest()


def _verified_archive(run, model):
    if run.get('status') != 'COMPLETED':
        raise ValueError('Only COMPLETED runs can be evaluated')
    if run.get('model_source', {}).get('sha256') != model.source['sha256']:
        raise ValueError('Run source SHA-256 mismatch')
    if run.get('model_fingerprint') != model.fingerprint():
        raise ValueError('Run model fingerprint mismatch')
    if run.get('original_directions') != model.directions:
        raise ValueError('Run objective directions mismatch')
    names = [variable.name for variable in model.variables]
    if run.get('variable_names') != names:
        raise ValueError('Run variable mapping mismatch')
    archive = run.get('archive')
    if not isinstance(archive, list):
        raise ValueError('Run archive must be a list')
    budget = number(run.get('budget'))
    if budget <= 0:
        raise ValueError('Run budget must be positive')
    objective_vectors = []
    for index, point in enumerate(archive):
        if not isinstance(point, dict) or 'x' not in point:
            raise ValueError(f'Archive point {index} lacks a full assignment')
        checked = model.validate(point['x'], names=names)
        if not checked['valid']:
            raise ValueError(f'Archive point {index} fails original-model validation')
        saved_original = vector(point.get('original'), len(model.objectives), 'original objective')
        saved_internal = vector(point.get('internal'), len(model.objectives), 'internal objective')
        if saved_original != tuple(checked['original']) or saved_internal != tuple(checked['internal']):
            raise ValueError(f'Archive point {index} has incorrect objective values')
        elapsed = number(point.get('validated_by_elapsed'))
        if elapsed < 0 or elapsed >= budget:
            raise ValueError(f'Archive point {index} lacks a pre-deadline validation stamp')
        objective_vectors.append(saved_internal)
    if len(nondominated(objective_vectors)) != len(objective_vectors):
        raise ValueError('Saved archive has duplicate or dominated objective vectors')
    return tuple(objective_vectors)


def evaluate_saved_run(run_path, spec_path, source_override=None):
    """Revalidate saved assignments and compute HV using a frozen JSON spec."""
    spec_path = Path(spec_path).resolve()
    spec, spec_sha = _read_json(spec_path)
    return _evaluate_saved_run(run_path, spec,
                               {'path':str(spec_path),'sha256':spec_sha},
                               source_override)


def evaluate_saved_run_reference(run_path, reference_original, source_override=None):
    """Convenience raw-coordinate HV with an explicit original-sense reference."""
    run, _ = _read_json(run_path)
    directions = run.get('original_directions')
    if not isinstance(directions,list) or not 2 <= len(directions) <= 4:
        raise ValueError('Saved run needs 2--4 objective directions')
    spec = {'schema':SCHEMA,
            'model_fingerprint':run.get('model_fingerprint'),
            'source_sha256':run.get('model_source',{}).get('sha256'),
            'objective_directions':directions,
            'normalization':{'origin_internal':['0']*len(directions),
                             'scale':['1']*len(directions)},
            'hv_reference_original':reference_original}
    return _evaluate_saved_run(run_path,spec,
                               {'kind':'inline_reference_original'},source_override)


def _evaluate_saved_run(run_path, spec, spec_record, source_override):
    run_path = Path(run_path).resolve()
    run, run_sha = _read_json(run_path)
    required = {'schema', 'model_fingerprint', 'source_sha256', 'objective_directions',
                'normalization', 'hv_reference_original'}
    if set(spec) != required or spec['schema'] != SCHEMA:
        raise ValueError('Unknown or incomplete HV specification')
    caps = input_limits(run.get('input_limits'))
    source = source_override if source_override is not None else run.get('source')
    if not isinstance(source, (str, Path)):
        raise ValueError('Original LP path is required; use --source after relocation')
    model = load_lp(source, max_bytes=caps['max_bytes'], max_terms=caps['max_terms'],
                    max_variables=caps['max_variables'], max_constraints=caps['max_constraints'],
                    max_expression_terms=caps['max_expression_terms'])
    if not 2 <= len(model.objectives) <= 4:
        raise ValueError('HV supports 2--4 objectives')
    if (spec['model_fingerprint'] != model.fingerprint()
            or spec['source_sha256'] != model.source['sha256']
            or spec['objective_directions'] != model.directions):
        raise ValueError('Metric specification does not describe this exact LP model')
    front = _verified_archive(run, model)
    dimensions = len(model.objectives)
    norm = spec['normalization']
    if not isinstance(norm, dict) or set(norm) != {'origin_internal', 'scale'}:
        raise ValueError('Normalization must specify fixed origin_internal and scale')
    origin = vector(norm['origin_internal'], dimensions, 'normalization origin')
    scale = vector(norm['scale'], dimensions, 'normalization scale')
    if any(value <= 0 for value in scale):
        raise ValueError('All normalization scales must be strictly positive')

    def to_internal(original):
        return original_to_internal(original, model.directions)

    def normalized(internal):
        return tuple((value - offset) / width
                     for value, offset, width in zip(internal, origin, scale))

    hv_reference_original = vector(spec['hv_reference_original'], dimensions, 'HV reference')
    hv_reference = normalized(to_internal(hv_reference_original))
    approximation = tuple(normalized(point) for point in front)
    hv = hypervolume(approximation, hv_reference)

    try:
        hv_value = float(hv)
    except OverflowError as exc:
        raise ValueError('HV normalized volume exceeds floating-point range') from exc
    if not math.isfinite(hv_value):
        raise ValueError('HV normalized volume exceeds floating-point range')
    return {
        'schema': SCHEMA,
        'run': {'path': str(run_path), 'sha256': run_sha,
                'source_sha256': model.source['sha256'],
                'model_fingerprint': model.fingerprint(),
                'implementation_fingerprint': run.get('implementation_fingerprint'),
                'points_independently_revalidated': len(front)},
        'spec': spec_record,
        'normalization': {'origin_internal': [str(value) for value in origin],
                          'scale': [str(value) for value in scale]},
        'hv': {'value': hv_value, 'exact_normalized_value': str(hv),
               'reference_original': [str(value) for value in hv_reference_original],
               'reference_normalized': [str(value) for value in hv_reference],
               'definition': 'exact union of dominated boxes in normalized minimization space'},
        'scope': 'offline HV for independently validated samples of the serialized LP; no Pareto optimality certificate'
    }
