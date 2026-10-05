"""Standalone Python missile calculator with separate versioned resources.

Only NumPy is required at runtime. Keep inputs/resources alongside this file.
Compiled fast mode optionally uses a local C compiler and a persistent cache.
Generated from the repository's Python kernel; edit source modules and rebuild
instead of hand-editing this derived file.
"""
from pathlib import Path
import csv
import json

ROOT = Path(__file__).resolve().parent


def write_json(path, obj, *, compact=False):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=None if compact else 2,
                                    separators=(',', ':') if compact else None, allow_nan=False) + '\n', encoding='utf-8')


def save(run, scenario, summary, rows):
    write_json(run / 'scenario.json', scenario)
    write_json(run / 'summary.json', summary)
    with (run / 'trajectory.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


# ---- game_math.py ----
"""Shared numerical functions for the independent game-logic port."""

import math
from itertools import pairwise

import numpy as np


def cross3(a, b):
    """Fixed 3-vector cross product without NumPy's generalized axis machinery."""
    return np.array(
        [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]
    )


def norm3(a):
    return math.sqrt(float(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]))


def _interp(points, x):
    if x <= points[0][0]:
        return float(points[0][1])
    for (a, va), (b, vb) in pairwise(points):
        if x <= b:
            return float(va + (vb - va) * (x - a) / (b - a))
    return float(points[-1][1])


def _table(config, prefix, default):
    values = [value for key, value in config.items() if key.startswith(prefix)]
    return sorted(values, key=lambda row: row[0]) or default


def _atmosphere(height):
    height = float(height)
    hp, hd = min(height, 18300.0), max(height, 18300.0)
    polynomial = (
        1
        - 9.59387e-5 * hp
        + 3.53118e-9 * hp**2
        - 5.83556e-14 * hp**3
        + 2.28719e-19 * hp**4
    )
    temperature = 288.16 * (
        1
        - 2.27712e-5 * hp
        + 2.18069e-10 * hp**2
        - 5.71104e-14 * hp**3
        + 3.97306e-18 * hp**4
    )
    return max(0.0, 1.225 * polynomial * 18300 / hd), 20.1 * math.sqrt(
        max(1.0, temperature)
    )


def _mach_function(mach):
    if mach < 0.61:
        return 0.308
    if mach < 1:
        return 0.308 + 0.505 * (mach - 0.61) ** 2.31
    if mach < 1.4:
        return 0.551 + 0.4485 * (mach - 1) ** 0.505 * math.exp(-5.68 * (mach - 1))
    if mach < 4:
        return mach / ((0.356 * mach + 2.237) * mach - 1.4)
    return 0.302


def _loft_elevation(autopilot, distance):
    if "rangeToLoftElevation" not in autopilot:
        return float(autopilot.get("loftElevation", 0))
    r1, e1, r2, e2 = autopilot["rangeToLoftElevation"]
    points = sorted(((r1 * r1, e1), (r2 * r2, e2)))
    if points[0][0] == points[1][0]:
        return float(
            points[0][1] if distance * distance <= points[0][0] else points[1][1]
        )
    return _interp(points, distance * distance)


def norm64(a):
    """Internal float64-vector norm; same dot reduction and binary64 sqrt."""
    return math.sqrt(a.dot(a))

# ---- timing.py ----
"""Version-scoped client object timing; not a server/runtime measurement."""

# 2.59.0.28 ELF 94de5454…: 0x925a4f0, read by 0x201fa17.
# Rocket vtable 0x9259878 -> slot 0x118 -> 0x201a660.
# Controller construction also selects this value (0x203e6d0 -> 0x5ee48e0).
# Preserve the game's float32 value, rather than an arbitrary finer time step.
CLIENT_OBJECT_DT_S = 0.02083333395421505


def default_dt(version):
    # Do not transfer .28 addresses/clock evidence to the older capture build.
    return CLIENT_OBJECT_DT_S if version == '2.59.0.28' else .002

# ---- scenario.py ----
"""Strict SI scenario input and analytic, piecewise target motion."""
import copy
from bisect import bisect_right
import math
import numpy as np


def vector(value, name, count=3):
    a = np.asarray(value, dtype=float)
    if a.shape != (count,) or not np.isfinite(a).all():
        raise ValueError(f'{name} must contain {count} finite numbers')
    return a


def rotation(q):
    """Native x,y,z,w quaternion; body-to-world matrix."""
    x, y, z, w = q
    return np.array([[1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
                     [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                     [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)]])


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('Scenario must be a JSON object')
    s = copy.deepcopy(raw)
    allowed = {'schema_version', 'missile', 'duration_s', 'dt_s', 'sample_period_s',
               'launch', 'target', 'wind_m_s', 'ground_height_m', 'proximity_fuse', 'label', 'observation', 'version', 'fastmode', 'step_policy'}
    if set(s) - allowed:
        raise ValueError(f'Unknown scenario keys: {sorted(set(s) - allowed)}')
    if s.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    if s.setdefault('version', '2.59.0.28') not in ('2.59.0.28', '2.59.0.22'):
        raise ValueError('No native adapter for requested version')
    if not isinstance(s.setdefault('fastmode', False), bool):
        raise ValueError('fastmode must be boolean')
    if s['fastmode'] and s['version'] != '2.59.0.28':
        raise ValueError('fastmode is verified only for 2.59.0.28')
    if s.get('step_policy', 'version_default') not in ('version_default', 'fixed'):
        raise ValueError('step_policy must be version_default or fixed')
    for key, default, lo, hi in [('duration_s', 60, .001, 600),
                                 ('dt_s', default_dt(s['version']), .00025, CLIENT_OBJECT_DT_S),
                                 ('sample_period_s', None, .00025, 1)]:
        raw_value = s.get(key, default)
        if key == 'sample_period_s' and raw_value is None:
            s[key] = None  # Actual integration endpoints, including short/event steps.
            continue
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f'{key} must be a finite number')
        value = float(raw_value)
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'{key} must be in [{lo}, {hi}]')
        s[key] = value
    obs = s.setdefault('observation', {})
    if not isinstance(obs, dict) or set(obs) - {'mode', 'sample_period_s', 'latency_s', 'position_bias_m', 'velocity_bias_m_s'}:
        raise ValueError('Invalid observation object or unknown observation keys')
    if obs.setdefault('mode', 'locked') not in ('locked', 'ideal'):
        raise ValueError('observation.mode must be locked or ideal')
    for key, default, lo, hi in [('sample_period_s', s['dt_s'], s['dt_s'], 10), ('latency_s', 0, 0, 30)]:
        value = float(obs.get(key, default))
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'observation.{key} must be in [{lo}, {hi}]')
        obs[key] = value
    for key in ('position_bias_m', 'velocity_bias_m_s'):
        obs[key] = vector(obs.get(key, [0, 0, 0]), f'observation.{key}').tolist()
    if obs['mode'] == 'ideal' and (obs['latency_s'] or any(obs['position_bias_m']) or any(obs['velocity_bias_m_s'])):
        raise ValueError('Ideal mode cannot silently ignore delay or observation bias')
    s['wind_m_s'] = vector(s.get('wind_m_s', [0, 0, 0]), 'wind_m_s').tolist()
    s['ground_height_m'] = float(s.get('ground_height_m', 0))
    if not math.isfinite(s['ground_height_m']):
        raise ValueError('ground_height_m must be finite')
    if not isinstance(s.get('proximity_fuse', True), bool):
        raise ValueError('proximity_fuse must be boolean')
    for name in ('launch', 'target'):
        if not isinstance(s.get(name), dict):
            raise ValueError(f'{name} must be an object')
        obj = s[name]
        keys = {'position_m', 'velocity_m_s'} | ({'quaternion_xyzw', 'angular_rate_rad_s', 'ejection_velocity_body_m_s', 'age_s', 'feedback_acceleration_m_s2', 'motor_start_times_s'}
                                              if name == 'launch' else {'collision_radius_m', 'maneuvers', 'samples', 'turn_rate_rad_s', 'interpolation'})
        if set(obj) - keys:
            raise ValueError(f'Unknown {name} keys: {sorted(set(obj) - keys)}')
        for key in ('position_m', 'velocity_m_s'):
            obj[key] = vector(obj.get(key), f'{name}.{key}').tolist()
    launch = s['launch']
    q = vector(launch.get('quaternion_xyzw', [0, 0, 0, 1]), 'launch.quaternion_xyzw', 4)
    if abs(float(q @ q) - 1) > 1e-4:
        raise ValueError('launch.quaternion_xyzw must be a unit quaternion')
    launch['quaternion_xyzw'] = (q / np.linalg.norm(q)).tolist()
    for key in ('angular_rate_rad_s', 'ejection_velocity_body_m_s', 'feedback_acceleration_m_s2'):
        launch[key] = vector(launch.get(key, [0, 0, 0]), f'launch.{key}').tolist()
    age = float(launch.get('age_s', 0))
    if not math.isfinite(age) or not 0 <= age < 600:
        raise ValueError('launch.age_s must be finite and within [0, 600)')
    launch['age_s'] = age
    if 'motor_start_times_s' in launch:
        times = launch['motor_start_times_s']
        if not isinstance(times, list) or not 1 <= len(times) <= 4 or any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0 or t > 600 for t in times):
            raise ValueError('motor_start_times_s requires 1..4 finite ages within [0, 600]')
    target = s['target']
    radius = float(target.get('collision_radius_m', 0))
    if not math.isfinite(radius) or radius < 0 or radius > 100:
        raise ValueError('target.collision_radius_m must be within [0, 100]')
    target['collision_radius_m'] = radius
    if 'turn_rate_rad_s' in target:
        rate = float(target['turn_rate_rad_s'])
        if not math.isfinite(rate) or abs(rate) > 100 or target.get('maneuvers') or 'samples' in target:
            raise ValueError('Invalid constant turn rate or conflicting target motion')
        target['turn_rate_rad_s'] = rate
    if not isinstance(target.get('maneuvers', []), list):
        raise ValueError('target.maneuvers must be an array')
    previous = -1
    for segment in target.setdefault('maneuvers', []):
        if not isinstance(segment, dict) or set(segment) != {'time_s', 'acceleration_m_s2'}:
            raise ValueError('Each maneuver requires time_s and acceleration_m_s2')
        time = float(segment['time_s'])
        if not math.isfinite(time) or time < 0 or time <= previous:
            raise ValueError('Maneuver times must be nonnegative and strictly increasing')
        segment['acceleration_m_s2'] = vector(segment['acceleration_m_s2'], 'maneuver acceleration').tolist()
        previous = time
    if target.get('interpolation', 'hermite') not in ('hermite', 'recorded_linear'):
        raise ValueError('target.interpolation must be hermite or recorded_linear')
    if 'interpolation' in target and 'samples' not in target:
        raise ValueError('target.interpolation requires recorded samples')
    if 'samples' in target:
        samples = target['samples']
        if not isinstance(samples, list) or len(samples) < 2 or target['maneuvers']:
            raise ValueError('Recorded target needs at least two samples and no maneuvers')
        previous = -1
        for sample in samples:
            required = {'time_s', 'position_m', 'velocity_m_s'}
            optional = {'left_position_m', 'left_velocity_m_s'}
            if (not isinstance(sample, dict) or not required <= set(sample) or
                    set(sample) - required - optional or
                    bool('left_position_m' in sample) != bool('left_velocity_m_s' in sample)):
                raise ValueError('Target sample requires time_s, position_m, velocity_m_s')
            time = float(sample['time_s'])
            if not math.isfinite(time) or time < 0 or time <= previous:
                raise ValueError('Target sample times must be nonnegative and strictly increasing')
            sample['time_s'] = time
            for key in ('position_m', 'velocity_m_s'):
                sample[key] = vector(sample[key], f'target sample {key}').tolist()
            if 'left_position_m' in sample:
                if time == 0:
                    raise ValueError('A target switch cannot precede the initial sample')
                for key in ('left_position_m', 'left_velocity_m_s'):
                    sample[key] = vector(sample[key], f'target sample {key}').tolist()
            previous = time
        if samples[0]['time_s'] != 0 or samples[-1]['time_s'] < s['duration_s']:
            raise ValueError('Recorded target must cover time zero through duration_s; no extrapolation')
        for key in ('position_m', 'velocity_m_s'):
            if target[key] != samples[0][key]:
                raise ValueError(f'target.{key} must equal the first recorded sample')
    return s


class Target:
    def __init__(self, spec):
        self.position = np.array(spec['position_m'], dtype=float)
        self.velocity = np.array(spec['velocity_m_s'], dtype=float)
        self.acceleration = np.zeros(3)
        self.time = 0.
        self.segments = spec['maneuvers']
        self.samples = spec.get('samples')
        self.interpolation = spec.get('interpolation', 'hermite')
        if self.samples:
            self.segments = [{'time_s': s['time_s']} for s in self.samples[1:]]
            self.sample_times = np.array([s['time_s'] for s in self.samples])
            self._sample_times_list = self.sample_times.tolist()
            self._sample_vectors = tuple(
                (np.array(s['position_m']), np.array(s['velocity_m_s']),
                 np.array(s.get('left_position_m', s['position_m'])),
                 np.array(s.get('left_velocity_m_s', s['velocity_m_s'])))
                for s in self.samples
            )
        self.index = 0
        self.turn_rate = spec.get('turn_rate_rad_s', 0.)
        self.initial_position = self.position.copy()
        self.initial_velocity = self.velocity.copy()

    def advance(self, end):
        if self.turn_rate:
            w = self.turn_rate
            angle = w * end
            c, sn = math.cos(angle), math.sin(angle)
            vx, vy, vz = self.initial_velocity
            # Positive heading rotates +x toward +z. Constant pitch and speed.
            self.velocity = np.array([vx*c-vz*sn, vy, vx*sn+vz*c])
            sinc = float(np.sinc(angle / math.pi))
            cosc = .5 * angle * float(np.sinc(angle / (2*math.pi)))**2
            self.position = self.initial_position + end*np.array([vx*sinc-vz*cosc, vy, vx*cosc+vz*sinc])
            self.acceleration = np.array([-w*self.velocity[2], 0., w*self.velocity[0]])
            self.time = end
            return
        if self.samples:
            self._recorded(end)
            return
        while self.index < len(self.segments) and self.segments[self.index]['time_s'] <= end:
            segment = self.segments[self.index]
            self._step(segment['time_s'])
            self.acceleration = np.array(segment['acceleration_m_s2'])
            self.index += 1
        self._step(end)

    def _recorded(self, end):
        if end < 0 or end > self.sample_times[-1] + 1e-9:
            raise ValueError('Recorded target time lies outside supplied samples')
        i = min(len(self.samples)-2, max(0, bisect_right(self._sample_times_list, end)-1))
        a, b = self.samples[i:i+2]
        h = b['time_s']-a['time_s']
        u = min(1., max(0., (end-a['time_s'])/h))
        p, v = self._sample_vectors[i][:2]
        q, w = self._sample_vectors[i+1][2:]
        if end >= self.sample_times[-1] and 'left_position_m' in b:
            self.position = np.array(b['position_m'], dtype=float)
            self.velocity = np.array(b['velocity_m_s'], dtype=float)
            self.acceleration = np.zeros(3)
            self.time = end
            return
        if self.interpolation == 'recorded_linear':
            # Recorded position and velocity are independent telemetry channels.
            # Differentiating jittered/repeated position samples invents velocities.
            self.position = p + u*(q-p)
            self.velocity = v + u*(w-v)
            self.acceleration = (w-v)/h
            self.time = end
            return
        # Cubic Hermite interpolation honours position AND velocity at knots.
        # This is an offline target replay adapter, not a game interpolation claim.
        self.position = (2*u**3-3*u*u+1)*p + (u**3-2*u*u+u)*h*v + (-2*u**3+3*u*u)*q + (u**3-u*u)*h*w
        self.velocity = (6*u*u-6*u)/h*p + (3*u*u-4*u+1)*v + (-6*u*u+6*u)/h*q + (3*u*u-2*u)*w
        self.acceleration = (12*u-6)/(h*h)*p + (6*u-4)/h*v + (-12*u+6)/(h*h)*q + (6*u-2)/h*w
        self.time = end

    def _step(self, end):
        dt = end - self.time
        self.position += self.velocity * dt + self.acceleration * (.5 * dt * dt)
        self.velocity += self.acceleration * dt
        self.time = end

# ---- events.py ----
"""Continuous segment events for the explicitly spherical/flat-world model."""
import math
import numpy as np


def closest_fraction(r0, r1):
    delta = np.asarray(r1) - r0
    d2 = float(delta @ delta)
    return float(np.clip(-np.asarray(r0) @ delta / d2, 0, 1)) if d2 else 0.


def sphere_entry(r0, r1, radius, start_fraction=0.):
    r0 = np.asarray(r0)
    delta = np.asarray(r1) - r0
    start = r0 + start_fraction * delta
    if float(start @ start) <= radius * radius:
        return start_fraction
    a = float(delta @ delta)
    if a == 0:
        return None
    b = float(r0 @ delta)
    c = float(r0 @ r0) - radius * radius
    discriminant = b*b - a*c
    if discriminant < 0:
        return None
    root = (-b - math.sqrt(max(0, discriminant))) / a
    return root if start_fraction <= root <= 1 else None


def first_event(t, dt, missile0, missile1, target0, target1, collision_radius, fuse_radius, fuse_arm, ground):
    r0, r1 = target0 - missile0, target1 - missile1
    candidates = []
    if collision_radius > 0:
        f = sphere_entry(r0, r1, collision_radius)
        if f is not None: candidates.append((f, 'contact'))
    if fuse_radius > 0 and t + dt >= fuse_arm:
        f = sphere_entry(r0, r1, fuse_radius, max(0., (fuse_arm - t) / dt))
        if f is not None: candidates.append((f, 'proximity_fuse'))
    if missile0[1] <= ground:
        candidates.append((0., 'ground'))
    elif missile1[1] <= ground:
        candidates.append((float((missile0[1] - ground) / (missile0[1] - missile1[1])), 'ground'))
    return min(candidates, default=None, key=lambda pair: (pair[0], pair[1]))

# ---- observation.py ----
"""Scene-supplied observation sampling, delay, and extrapolation."""

import math
from collections import deque

import numpy as np


class ObservationTransport:
    """Timestamped delayed observations, with constant-velocity extrapolation.

    The t=0 observation seeds an already acquired track. Later observations wait
    for their configured arrival, and can never reveal a maneuver before arrival.
    """

    def __init__(self, spec, position, velocity):
        self.spec = spec
        self.pending = deque()
        self.latest = (
            0.0,
            np.array(position) + spec["position_bias_m"],
            np.array(velocity) + spec["velocity_bias_m_s"],
        )
        self.next_capture = spec["sample_period_s"]
        self.count = 1
        self.received = 1

    def boundary(self):
        return min(self.next_capture, self.pending[0][0] if self.pending else math.inf)

    def update(self, t, position, velocity):
        if t + 1e-9 >= self.next_capture:
            packet = (
                t,
                np.array(position) + self.spec["position_bias_m"],
                np.array(velocity) + self.spec["velocity_bias_m_s"],
            )
            self.pending.append((t + self.spec["latency_s"], packet))
            self.count += 1
            self.next_capture += self.spec["sample_period_s"]
        while self.pending and self.pending[0][0] <= t + 1e-9:
            _, self.latest = self.pending.popleft()
            self.received += 1
        stamp, p, v = self.latest
        return p + v * (t - stamp), v.copy(), max(0.0, t - stamp)

# ---- resources.py ----
"""Read immutable, same-build BLK snapshots without executing archived scripts."""
from pathlib import Path
import hashlib
import json
import re


VERSION = '2.59.0.28'
ELF_SHA256 = '94de54548da8eb616f391d04e117ca11c8cf7143e7c6be6dfb9468c40512591a'
RAW = ROOT / 'inputs/resources/2.59.0.28'
_catalog = (RAW / 'presets.json').read_bytes()
if hashlib.sha256(_catalog).hexdigest() != json.loads((RAW / 'manifest.json').read_text())['files']['presets.json']:
    raise ValueError('Preset catalog identity mismatch')
PRESETS = json.loads(_catalog)['presets']
PROFILES = tuple(PRESETS)
PROFILES_BY_VERSION = {'2.59.0.28': PROFILES}
ELF_BY_VERSION = {'2.59.0.28': ELF_SHA256,
                  '2.59.0.22': '416b45f94c43b3a692bc3a58c5bcf231ffae774ebb137a6866d94a8aa47df35c'}


def parse_blk(path):
    """Preserve repeated blocks/keys as lists; never silently discard evidence."""
    root = {}
    stack = [root]
    repeated = set()
    def insert(key, value):
        target = stack[-1]
        if key in target:
            previous = target[key]
            if (id(target), key) not in repeated:
                target[key] = [previous]
                repeated.add((id(target), key))
            target[key].append(value)
        else:
            target[key] = value
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        if line.endswith('{'):
            block = {}
            insert(line[:-1].strip(), block)
            stack.append(block)
        elif line == '}':
            if len(stack) == 1:
                raise ValueError(f'{path}:{number}: unmatched closing brace')
            stack.pop()
        else:
            match = re.fullmatch(r'(\w+):(\w+)\s*=\s*(.*)', line)
            if not match:
                raise ValueError(f'{path}:{number}: unsupported BLK syntax')
            key, kind, value = match.groups()
            if kind == 'r': value = float(value)
            elif kind == 'i': value = int(value)
            elif kind == 'b':
                if value not in ('true', 'false', 'yes', 'no', '1', '0'):
                    raise ValueError(f'{path}:{number}: invalid boolean')
                value = value in ('true', 'yes', '1')
            elif kind.startswith('ip'): value = tuple(int(x) for x in value.split(','))
            elif kind.startswith('p'): value = tuple(float(x) for x in value.split(','))
            elif kind == 't': value = value.removeprefix('"').removesuffix('"')
            else: raise ValueError(f'{path}:{number}: unsupported type {kind}')
            insert(key, value)
    if len(stack) != 1:
        raise ValueError(f'{path}: unclosed block')
    return root


def load_profile(name, version=VERSION):
    profiles = PROFILES_BY_VERSION.get(version, ())
    if name not in profiles:
        raise ValueError(f'Unsupported missile {name} in {version}; available: {", ".join(profiles)}')
    folder = ROOT / 'inputs/resources' / version
    path = folder / f'gamedata__weapons__rocketguns__{name}.blk'
    raw = path.read_bytes()
    manifest = json.loads((folder / 'manifest.json').read_text())
    digest = hashlib.sha256(raw).hexdigest()
    if manifest['version'] != version or manifest['files'][path.name] != digest:
        raise ValueError(f'Resource identity mismatch: {path}')
    return parse_blk(path)['rocket'], {
        'missile': name, 'version': version, 'elf_sha256': ELF_BY_VERSION[version],
        'resource': str(path.relative_to(ROOT)), 'resource_sha256': digest,
        'archive_sha256': manifest['archive_sha256'],
    }

# ---- game_engine.py ----
"""Independent finite-stage propulsion, mass loss, TVC and pressure correction.

Port of the supported factorIndex=-1 branch of 85da410 / 85dad80 (.28).
Separate motors retain their own start clocks; automatic second-pulse
controllers must not be substituted by this explicit ignition schedule.
"""

import math

import numpy as np

F = np.float32


def pressure(height):
    hp, hd = F(min(height, 18300)), F(max(height, 18300))
    value = F(F(F(1.60373e-18) * hp) - F(1.3738e-13))
    value = F(F(value * hp) + F(5.6763e-9))
    value = F(F(value * hp) - F(0.000118441))
    value = F(F(value * hp) + F(1))
    return F(F(value * F(F(18300) / hd)) * F(101300))


class Engine:
    def __init__(self, params, start_times=None):
        self.p = p = params
        motors = []
        blocks = sorted((k, v) for k, v in p.items() if k.startswith("propulsion"))
        if blocks:
            if [k for k, _ in blocks] != [f"propulsion{i}" for i in range(len(blocks))]:
                raise ValueError("Non-contiguous propulsion blocks")
            for _, block in blocks:
                if set(block) - {
                    "fireDelay",
                    "impulse0",
                    "impulse1",
                    "impulse2",
                    "impulse3",
                }:
                    raise ValueError("Unsupported propulsion fields")
                stages = []
                for key in sorted(block):
                    if key.startswith("impulse"):
                        row = block[key]
                        if set(row) - {
                            "time",
                            "force",
                            "massLost",
                            "thrustVectoringAngle",
                        }:
                            raise ValueError("Unsupported propulsion impulse fields")
                        stages.append(
                            (
                                row["time"],
                                row.get("force", 0),
                                row.get("massLost", 0),
                                row.get("thrustVectoringAngle", 0),
                            )
                        )
                motors.append((block.get("fireDelay", 0), stages))
        else:
            stages, mass = [], p["mass"]
            for suffix in ("", "1"):
                duration = p.get("timeFire" + suffix, 0)
                if duration <= 0:
                    continue
                delay = p.get("fireDelay" + suffix, 0) if suffix else 0
                if delay:
                    stages.append((delay, 0, 0, 0))
                end_mass = p.get("massEnd" + suffix, mass)
                stages.append(
                    (
                        duration,
                        p.get("force" + suffix, 0),
                        mass - end_mass,
                        p.get("thrustVectoringAngle" + suffix, 0),
                    )
                )
                mass = end_mass
            motors.append((p.get("fireDelay", 0), stages))
        if not 1 <= len(motors) <= 4 or any(not 1 <= len(s) <= 4 for _, s in motors):
            raise ValueError("Supported engine layout requires 1..4 motors and stages")
        if start_times is not None and len(start_times) != len(motors):
            raise ValueError("motor_start_times_s must provide one start age per motor")
        self.motors, self.boundaries, self.start_times = [], [], []
        elapsed = 0.0
        for i, (delay, stages) in enumerate(motors):
            start = (start_times[i] if start_times is not None else elapsed) + delay
            self.start_times.append(start)
            self.boundaries.append(start)
            elapsed = start
            for duration, force, fuel, tvc in stages:
                if duration <= 0 or force < 0 or fuel < 0:
                    raise ValueError("Invalid propulsion stage")
                elapsed += duration
                self.boundaries.append(elapsed)
            self.motors.append((F(start), tuple(tuple(map(F, s)) for s in stages)))
        if sum(s[2] for _, stages in motors for s in stages) >= p["mass"]:
            raise ValueError("Engine fuel consumes entire missile mass")
        # Preserve the original float32 addition order, but compute immutable
        # stage boundaries and prior fuel totals only once per engine.
        self.prepared_motors = []
        for start, stages in self.motors:
            prepared = []
            end, spent = F(0), F(0)
            for duration, force, fuel, angle in stages:
                next_end = F(end + duration)
                prepared.append((end, next_end, spent, duration, force, fuel, angle))
                end, spent = next_end, F(spent + fuel)
            self.prepared_motors.append((start, tuple(prepared), spent))
        self.pressure_curve = None
        if 'extPressureToThrustMult' in p:
            x0, y0, x1, y1 = map(F, p['extPressureToThrustMult'])
            if x0 > x1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            self.pressure_curve = x0, y0, x1, y1
        self.lever = p['length'] * 0.5

    def evaluate(self, age, controls=(0, 0), height=0.0):
        thrust, lost, tvc = F(0), F(0), F(0)
        age = F(age)
        for start, stages, total_fuel in self.prepared_motors:
            local = F(age - start)
            spent = total_fuel
            force, angle = F(0), F(0)
            for end, next_end, prior_fuel, duration, force_value, fuel, angle_value in stages:
                if local < next_end:
                    spent = prior_fuel
                    within = F(local - end)
                    if within > 0:
                        force, angle = force_value, angle_value
                        spent = F(spent + F(F(within / duration) * fuel))
                    break
            thrust, lost, tvc = F(thrust + force), F(lost + spent), F(tvc + angle)
        mult = F(1)
        if self.pressure_curve is not None:
            x0, y0, x1, y1 = self.pressure_curve
            x = pressure(height)
            mult = y0 if x <= x0 else y1
            if x0 < x < x1:
                mult = F(y0 + F(F(F(y1 - y0) * F(x - x0)) / F(x1 - x0)))
        effective_thrust = F(thrust * mult)
        h, v = F(F(controls[0]) * tvc), F(F(controls[1]) * tvc)
        axial = F(
            F(math.sqrt(max(0.0, float(F(F(1) - F(F(h * h) + F(v * v)))))))
            * effective_thrust
        )
        force = np.array(
            [axial, F(-v * effective_thrust), F(-h * effective_thrust)], dtype=float
        )
        lever = self.lever
        torque = np.array([0.0, -lever * force[2], lever * force[1]])
        return self.p["mass"] - float(lost), float(thrust), float(tvc), force, torque

# ---- game_body.py ----
"""Direct mathematical port of the .28 air/constant-Cy rigid-body branch.

Recovery: 85d3cf0, ELF 94de5454…; source and differential probes are in
runs/python-game-kernel-20260926. No executable or emulator is used here.
Native angular-rate signs differ from a conventional right-handed body rate.
"""

import math

import numpy as np



def unit(v, fallback):
    length = norm3(v)
    return v / length if length > 1e-9 else np.asarray(fallback).copy()


def quaternion_product(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return np.array(
        [
            w * X + x * W + y * Z - z * Y,
            w * Y + y * W + z * X - x * Z,
            w * Z + z * W + x * Y - y * X,
            w * W - x * X - y * Y - z * Z,
        ]
    )


def advance_attitude(q, native_angle):
    # 85d3ed7: negate angular displacement, then ordered Euler increment.
    x, y, z = -0.5 * np.asarray(native_angle)
    sx, sy, sz = math.sin(x), math.sin(y), math.sin(z)
    cx, cy, cz = math.cos(x), math.cos(y), math.cos(z)
    # Rearrangement from recovered SIMD lane order (y,z,x).
    delta = [
        cx * sy * sz + cy * cz * sx,
        cx * cz * sy + cy * sx * sz,
        cx * cy * sz - sy * sx * cz,
        cx * cy * cz - sy * sx * sz,
    ]
    result = quaternion_product(q, delta)
    return result / norm64(result)


class Body:
    def __init__(self, params):
        p = params
        self.p = p
        d, L, m, w = (p[k] for k in ("caliber", "length", "mass", "wingAreaMult"))
        self.lift_area = 0.3 * d * L * w
        self.drag_area = math.pi * d * d / 4
        self.lever = p.get("distFromCmToStab", 0.3 * L) / w
        self.cy = p.get("CyK", 2.2)
        self.cy_peak = p.get("CyMaxAoA", 1.0)
        self.fins = np.array([p["finsAoaHor"], p["finsAoaVer"]])
        self.control_limit = (
            m * 9.81 * p.get("finsLatAccel", 100) / (self.cy * self.lift_area)
        )
        self.inertia = np.array(
            [
                m * (d / 2) ** 2,
                m * (L * L + 3 * (d / 2) ** 2) / 12 * p.get("inertiaScale", 1),
                m * (L * L + 3 * (d / 2) ** 2) / 12 * p.get("inertiaScale", 1),
            ]
        )
        self.damping = (
            np.array(p.get("WdK", [1, 1, 1]))
            * L
            * L
            * self.drag_area
            * np.array([-0.01, -0.05, -0.05])
        )
        self.cx_aoa = p.get('CxAoA', 9.0) * (
            p['wingAreaMult'] ** 2 if p.get('applyWingAreaMultToCxAoA', False) else 1.0
        )

    def forces(self, state, dt, mass, force_world, torque_body, wind, gravity, *, body_rotation=None):
        p = self.p
        pos, q, vel, rate = state[:3], state[3:7], state[7:10], state[10:13]
        R = rotation(q) if body_rotation is None else body_rotation
        nose = unit(R[:, 0], [1.0, 0.0, 0.0])
        air = vel - np.asarray(wind)
        speed = norm3(air)
        rho, sound = _atmosphere(pos[1])
        dynamic_pressure = 0.5 * rho * speed * speed
        local = air + R @ np.array([0.0, self.lever * rate[2], -self.lever * rate[1]])
        flow = unit(local, unit(air, nose))
        fm = _mach_function(speed / sound)
        induced = self.cx_aoa * (
            3.247 * (0.308 + 0.75 * (fm - 0.308)) if p.get("useCxiMach", True) else 1.0
        )

        def aero(direction):
            cosine = float(nose @ direction)
            sin2 = max(0.0, 1.0 - cosine * cosine)
            cy = self.cy * math.sqrt(sin2)
            if cy > self.cy_peak:
                cy = max(0.0, 2 * self.cy_peak - cy)
            lift = cy * dynamic_pressure * self.lift_area
            if cosine >= 0:
                lift = -lift
            lift_direction = unit(direction * cosine - nose, [0.0, 0.0, 0.0])
            drag = dynamic_pressure * self.drag_area * p["CxK"] * (fm + induced * sin2)
            return direction * (-drag) + lift_direction * lift

        base_force = aero(flow)
        controls = self.controls * self.fins
        size = norm64(controls)
        if dynamic_pressure * size > self.control_limit:
            controls *= self.control_limit / (dynamic_pressure * size)
        if abs(self.controls[0]) * self.fins[0] > 1e-5 or abs(self.controls[1]) * self.fins[1] > 1e-5:
            control_flow = unit(
                flow + R[:, 2] * controls[0] + R[:, 1] * controls[1], nose
            )
            torque_force = aero(control_flow)
        else:
            torque_force = base_force
        body_force = R.T @ torque_force
        moment = np.asarray(torque_body) + np.array(
            [0.0, -self.lever * body_force[2], self.lever * body_force[1]]
        )
        damping = self.damping * dynamic_pressure * rate
        stopping = self.inertia * rate / dt + moment
        damping = np.where(np.abs(damping) <= np.abs(stopping), damping, -stopping)
        angular = (cross3(rate, self.inertia * rate) + moment + damping) / self.inertia
        acceleration = (np.asarray(force_world) + base_force) / mass
        if gravity:
            acceleration += [0.0, -9.81, 0.0]
        length = norm3(acceleration)
        if length >= 6000:
            acceleration *= 6000 / length
        return acceleration, angular

    def step(
        self,
        state,
        dt,
        mass,
        force_world,
        torque_body=(0, 0, 0),
        wind=(0, 0, 0),
        controls=(0, 0),
        gravity=True,
        *,
        body_rotation=None,
    ):
        self.controls = np.asarray(controls, dtype=float)
        acc, angular = self.forces(
            state, dt, mass, force_world, torque_body, wind, gravity,
            body_rotation=body_rotation,
        )
        next_state = state.copy()
        f = np.float32
        dt32 = f(dt)
        dt64 = float(dt32)
        # Keep every existing float32 rounding point and operation order.
        # The two float32 arrays add directly without a redundant dtype cast.
        next_state[:3] = state[:3] + (
            dt32 * state[7:10].astype(f) + f(float(f(dt32 * dt32 * f(0.5))) * acc)
        )
        next_state[7:10] = state[7:10] + f(dt64 * acc)
        next_state[10:13] = state[10:13] + f(dt64 * angular)
        next_state[3:7] = advance_attitude(
            state[3:7], state[10:13] * dt + 0.5 * dt * dt * angular
        )
        return next_state.astype(np.float32).astype(float), acc, angular

# ---- game_control.py ----
"""Recovered PN/loft and two-axis PID, using persistent controller state.

.28: 5f54560 / 5f587c0. This module does not use an ELF or Unicorn.
"""

import math

import numpy as np



def control_frame(q, velocity, velocity_reference, body_rotation=None):
    R = rotation(q) if body_rotation is None else body_rotation
    if not velocity_reference:
        return R
    a, b = unit(R[:, 0], [1.0, 0.0, 0.0]), unit(velocity, R[:, 0])
    cross = cross3(a, b)
    dot = float(a @ b)
    if dot < float(np.float32(-0.9999)):
        axis = unit(
            cross3(
                a, [0.0, 0.0, 1.0] if abs(a[2]) <= math.sqrt(0.5) else [0.0, 1.0, 0.0]
            ),
            [0.0, 1.0, 0.0],
        )
        transform = 2 * np.outer(axis, axis) - np.eye(3)
    else:
        x, y, z = cross
        K = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
        transform = np.eye(3) + K + (K @ K) / (1 + dot)
    # 5f59fc5 composes body quaternion * shortest-arc quaternion. Keep the
    # recovered order even though premultiplication looks more conventional.
    return R @ transform


class Controller:
    def __init__(self, params):
        self.p = params
        a = self.a = params["guidance"]["guidanceAutopilot"]
        self.age_gain = _table(
            a,
            "timeToGain",
            [(a.get("timeOut", 0) - 0.001, 0), (a.get("timeOut", 0), 1)],
        )
        self.hit_gain = _table(a, "timeToHitToGain", [(0, 1)])
        self.schedule = sorted(
            (v for k, v in a.items() if k.startswith("pid") and isinstance(v, dict)),
            key=lambda v: v["time"],
        )
        self.integral = np.zeros(2, dtype=np.float32)
        self.pid_tables = tuple(
            tuple((v['time'], v[k]) for v in self.schedule)
            for k in ('prop', 'intg', 'diff', 'intgLim')
        )
        self.derivative = np.zeros(2, dtype=np.float32)
        self.previous_error = np.zeros(2, dtype=np.float32)
        self.loft_active = bool(a.get("loftEnabled", False))
        self.gain = 0.0
        self.fast = False

    def pid(self, age, dt, request, state, feedback, *, engine_mass=None, thrust=0.0, body_rotation=None):
        a = self.a
        R = control_frame(state[3:7], state[7:10], a.get("velFrameReference", True), body_rotation)
        desired = (R.T @ request)[[2, 1]]
        cap = a.get("reqAccelMax", a.get("propNavAccelMax", 1000)) * 9.81
        rho, _ = _atmosphere(state[1])
        speed = norm3(state[7:10])
        if a.get("limitAoa", False):
            mass = self.p["mass"] if engine_mass is None else engine_mass
            area = 0.3 * self.p["caliber"] * self.p["length"] * self.p["wingAreaMult"]
            max_angle = min(math.radians(a.get("aoaMax", 0)), self.p.get("CyMaxAoA", 1))
            available = (
                (self.p.get("CyK", 2.2) * area * 0.5 * speed * speed * rho + thrust)
                * max_angle
                / mass
            )
            cap = min(cap, available)
        length = float(norm64(desired))
        if length > cap:
            desired *= cap / length
        f = np.float32
        error = np.asarray(desired - (R.T @ feedback)[[2, 1]], dtype=f)
        base = a.get("baseIndSpeed", 0) / 3.6
        scale2 = (
            f((1.225 * base) ** 2 / (rho * rho * speed * speed))
            if base > 0 and speed > 1e-19
            else f(0 if base > 0 else 1)
        )
        scale = f(np.sqrt(scale2))
        if self.schedule:
            gains = [_interp(table, age) for table in self.pid_tables]
        else:
            gains = [
                a.get("accelControl" + k, d)
                for k, d in [("Prop", 0.001), ("Intg", 0), ("Diff", 0), ("IntgLim", 1)]
            ]
        kp, ki, kd, limit = map(f, gains)
        kp, ki, kd = f(kp * scale), f(ki * scale), f(kd * scale2)
        if self.fast:
            kp, ki, kd, limit = map(float, (kp, ki, kd, limit))
            integral = []
            derivative = []
            output = []
            for i in range(2):
                e = float(error[i])
                I = min(limit, max(-limit, float(self.integral[i]) + dt * e * ki))
                D = (
                    e - (dt * float(self.derivative[i]) + float(self.previous_error[i]))
                ) * 48 + float(self.derivative[i])
                integral.append(I)
                derivative.append(D)
                output.append(min(1.0, max(-1.0, e * kp + D * kd + I)))
            self.integral = np.asarray(integral, dtype=f)
            self.derivative = np.asarray(derivative, dtype=f)
            self.previous_error = error
            return np.asarray(output, dtype=f).astype(float)
        # All operands below are float32 arrays/scalars. Each NumPy ufunc
        # already rounds to float32; recasting every temporary adds no rounding.
        dt32 = f(dt)
        self.integral = np.clip((dt32 * error) * ki + self.integral, -limit, limit)
        self.derivative = (
            (error - (dt32 * self.derivative + self.previous_error)) * f(48)
            + self.derivative
        )
        self.previous_error = error
        return np.clip(
            (error * kp + self.derivative * kd) + self.integral, -1, 1
        ).astype(float)

    def request(self, age, state, observation, reference_velocity, *, body_rotation=None):
        a = self.a
        u = np.asarray(observation["direction"])
        omega = -np.asarray(observation["omega"])
        distance, closing = observation["range_m"], observation["closing_m_s"]
        tgo = distance / closing if abs(closing) > 4e-19 else 0.0
        self.gain = _interp(self.age_gain, age) * _interp(self.hit_gain, tgo)
        if self.gain < 0.001:
            return np.zeros(3)
        nav = a.get("propNavMult", 4)
        angle_gain = (
            nav if a.get("purePursuit", False) else a.get("angleToAccelMult", 0)
        )
        omega_gain = (
            0 if a.get("purePursuit", False) else a.get("omegaToAccelMult", nav)
        )
        effective = self.gain * omega_gain * omega
        if angle_gain:
            if body_rotation is None:
                body_rotation = rotation(state[3:7])
            local = body_rotation.T @ u
            pitch = math.atan2(local[1], math.hypot(local[0], local[2]))
            yaw = math.atan2(-local[2], local[0])
            effective[2] += (
                self.gain * angle_gain * (pitch + math.radians(a.get("angleBias", 0)))
            )
            effective[1] += self.gain * angle_gain * yaw
        request = cross3(effective, reference_velocity)
        if self.loft_active:
            tan = math.tan(math.radians(a.get("loftTargetElevation", -89.9)))
            if (
                u[1] * abs(u[1]) < (u[0] * u[0] + u[2] * u[2]) * tan * abs(tan)
                or float(omega @ omega)
                > math.radians(a.get("loftTargetOmegaMax", 0)) ** 2
            ):
                self.loft_active = False
            else:
                if body_rotation is None:
                    body_rotation = rotation(state[3:7])
                forward = body_rotation[:, 0]
                pitch = math.degrees(
                    math.atan2(forward[1], math.hypot(forward[0], forward[2]))
                )
                request[1] = max(
                    request[1],
                    a.get("loftAngleToAccelMult", 0)
                    * (_loft_elevation(a, distance) - pitch),
                )
        return request.astype(np.float32).astype(float)

# ---- game_seeker.py ----
"""Python port of the recovered angle/range/Doppler tracking filters.

The same permanent-lock exemptions as NativeSeeker apply. Observation
transport remains an explicit scenario input, not a reconstructed datalink.
"""

import math
from functools import lru_cache

import numpy as np


F = np.float32


@lru_cache(maxsize=128)
def filter_gains(dt, alpha, beta):
    k = min(F(48) * F(dt), F(10))
    return (
        F(1) - F(math.pow(float(F(1) - F(alpha)), float(k))),
        F(1) - F(math.pow(float(F(1) - F(beta)), float(F(k * k)))),
    )


def scalar_filter(value, derivative, measured, dt, channel, gain):
    return prepared_scalar_filter(value, derivative, measured, dt, prepare_scalar_filter(channel, gain))


def prepare_scalar_filter(channel, gain):
    low, high = F(channel['minValue']), F(channel['maxValue'])
    half_width = F(F(0.5) * F(channel['width']))
    return low, high, F(low + half_width), F(high - half_width), gain['filterAlpha'], gain['filterBetta']


def prepared_scalar_filter(value, derivative, measured, dt, prepared):
    low, high, gate_low, gate_high, gain_alpha, gain_beta = prepared
    dt = F(dt)
    measurement = F(min(max(F(measured), low), high))
    alpha, beta = filter_gains(float(dt), gain_alpha, gain_beta)
    prediction = F(F(dt * derivative) + value)
    error = F(measurement - prediction)
    derivative = F(derivative + F(F(F(1) / dt) * F(beta * error)))
    value = F(prediction + F(alpha * error))
    value = F(min(max(value, gate_low), gate_high))
    return value, derivative


class Seeker:
    def __init__(self, missile, ground_height):
        self.n = missile
        self._body_rotation = getattr(missile, 'body_rotation', None)
        guidance = missile.params["guidance"]
        self.radar = "radarSeeker" in guidance
        self.p = guidance["radarSeeker" if self.radar else "opticalSeeker"]
        self.ground = ground_height
        self.started = False
        self.last_time = None
        self.updates = self.multipath_updates = 0
        self.observation = None
        self.fast = False
        self.predictions = 0
        self.source_age_at_filter = 0.0
        self.fallback_reason = None
        self.range_valid = bool(self.radar and self.p.get('distance', {}).get('presents', False))
        self.doppler_valid = bool(self.radar and self.p.get('dopplerSpeed', {}).get('presents', False))
        self.range_filter = prepare_scalar_filter(self.p['distance'], self.p['distGate']) if self.range_valid else None
        self.doppler_filter = prepare_scalar_filter(self.p['dopplerSpeed'], self.p['dopplerSpeedGate']) if self.doppler_valid else None
        self.alpha = F(self.p.get('filterAlpha', 0.85))
        self.beta = F(self.p.get('filterBetta', 0.2))
        curve = self.p.get('multipathEffect', (0, 0, 0, 0)) if self.radar else (0, 0, 0, 0)
        if isinstance(curve, list):
            if not curve or any(x != curve[0] for x in curve):
                raise ValueError('Conflicting duplicate multipath configuration')
            curve = curve[0]
        self.multipath_curve = tuple(map(F, curve))
        self.ground_f32 = F(ground_height)

    def update(self, age, dt, target_position, target_velocity):
        state = self.n.state()
        measured = np.asarray(target_position, dtype=F).copy()
        if self.radar:
            h = max(F(measured[1] - self.ground_f32), F(0))
            lo, a, hi, b = self.multipath_curve
            if h <= hi:
                value = a if lo <= hi else b
                if lo < h:
                    value = b
                    if h != hi and abs(hi - lo) > 4e-19:
                        value = F(F(F(b - a) * F(h - lo)) / F(hi - lo) + a)
                old = measured[1]
                measured[1] = F(measured[1] + F(F(h * F(-2)) * value))
                self.multipath_updates += int(abs(float(old - measured[1])) > 1e-6)
        relative = measured.astype(float) - state[:3]
        distance = float(norm64(relative))
        direction = relative / max(distance, 1e-12)
        closing = -float((np.asarray(target_velocity) - state[7:10]) @ direction)
        if not self.started:
            self.direction = direction.astype(F)
            self.omega = np.zeros(3, dtype=F)
            self.range_value, self.range_derivative = F(distance), F(-closing)
            self.closing_value, self.closing_derivative = F(closing), F(0)
            self.started = True
        else:
            dt = F(age - self.last_time)
            if dt <= 0:
                raise ValueError("Seeker timestamps must strictly increase")
            if self.fast:
                predicted = unit(
                    self.direction + cross3(self.direction, self.omega) * float(dt),
                    [0.0, 0.0, 0.0],
                )
                innovation = cross3(direction - predicted, predicted)
                self.direction = unit(
                    predicted
                    + cross3(predicted, innovation) * self.p.get("filterAlpha", 0.85),
                    [0.0, 0.0, 0.0],
                )
                self.omega = self.omega + innovation * (
                    self.p.get("filterBetta", 0.2) / float(dt)
                )
            else:
                R = self._body_rotation() if self._body_rotation is not None else rotation(state[3:7])
                body_direction = (R.T @ direction).astype(F)
                measurement = (R @ body_direction).astype(F)
                predicted = unit(
                    F(self.direction + F(cross3(self.direction, self.omega) * dt)),
                    [0.0, 0.0, 0.0],
                ).astype(F)
                innovation = cross3(F(measurement - predicted), predicted).astype(F)
                self.direction = unit(
                    F(
                        predicted
                        + F(
                            cross3(predicted, innovation)
                            * self.alpha
                        )
                    ),
                    [0.0, 0.0, 0.0],
                ).astype(F)
                self.omega = F(
                    self.omega
                    + F(
                        innovation * F(F(F(1) / dt) * self.beta)
                    )
                )
            if self.radar:
                if self.range_valid:
                    self.range_value, self.range_derivative = prepared_scalar_filter(
                        self.range_value,
                        self.range_derivative,
                        distance,
                        dt,
                        self.range_filter,
                    )
                if self.doppler_valid:
                    self.closing_value, self.closing_derivative = prepared_scalar_filter(
                        self.closing_value,
                        self.closing_derivative,
                        closing,
                        dt,
                        self.doppler_filter,
                    )
        rv, cv = self.range_valid, self.doppler_valid
        self.observation = {
            "direction": self.direction.astype(float),
            "omega": self.omega.astype(float),
            "range_m": float(self.range_value) if rv else distance,
            "closing_m_s": float(self.closing_value) if cv else closing,
            "range_valid": bool(rv),
            "doppler_valid": bool(cv),
            "measured_position": measured.astype(float),
        }
        self.updates += 1
        self.last_time = age
        return self.observation

# ---- game_fast.py ----
"""Faster scalar evaluation of the same rigid-body logic at every game tick.

No observation decimation, point-mass closure, or enlarged integration step.
Fast mode relaxes intermediate rounding and avoids redundant frame transforms.
"""

import math

import numpy as np



def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def normalized(v, fallback):
    length = math.sqrt(dot(v, v))
    return tuple(x / length for x in v) if length > 1e-9 else fallback


class FastSeeker(Seeker):
    def __init__(self, missile, ground):
        super().__init__(missile, ground)
        self.fast = True
        self.predictions = 0
        self.source_age_at_filter = 0.0
        self.fallback_reason = None


class FastBody(Body):
    def __init__(self, params):
        super().__init__(params)
        self.scalar_inertia = tuple(map(float, self.inertia))
        self.scalar_damping = tuple(map(float, self.damping))
        self.scalar_fins = tuple(map(float, self.fins))
        self.cx_aoa = params.get("CxAoA", 9) * (
            params["wingAreaMult"] ** 2
            if params.get("applyWingAreaMultToCxAoA", False)
            else 1.0
        )

    def forces(self, state, dt, mass, force_world, torque_body, wind, gravity, *, body_rotation=None):
        p = self.p
        rows = (rotation(state[3:7]) if body_rotation is None else body_rotation).tolist()
        axes = list(zip(*rows))
        nose = normalized(axes[0], (1.0, 0.0, 0.0))
        rate = tuple(map(float, state[10:13]))
        air = tuple(float(state[7 + i]) - float(wind[i]) for i in range(3))
        speed = math.sqrt(dot(air, air))
        rho, sound = _atmosphere(float(state[1]))
        dynamic = 0.5 * rho * speed * speed
        lever = self.lever
        flow = normalized(
            tuple(
                air[i] + lever * (rows[i][1] * rate[2] - rows[i][2] * rate[1])
                for i in range(3)
            ),
            normalized(air, nose),
        )
        fm = _mach_function(speed / sound)
        induced = self.cx_aoa * (
            3.247 * (0.308 + 0.75 * (fm - 0.308)) if p.get("useCxiMach", True) else 1.0
        )
        lift_scale = dynamic * self.lift_area
        drag_scale = dynamic * self.drag_area * p["CxK"]

        def aero(direction):
            cosine = dot(nose, direction)
            sin2 = max(0.0, 1 - cosine * cosine)
            cy = self.cy * math.sqrt(sin2)
            if cy > self.cy_peak:
                cy = max(0.0, 2 * self.cy_peak - cy)
            lift = cy * lift_scale * (-1 if cosine >= 0 else 1)
            lift_direction = normalized(
                tuple(direction[i] * cosine - nose[i] for i in range(3)),
                (0.0, 0.0, 0.0),
            )
            drag = drag_scale * (fm + induced * sin2)
            return tuple(
                -drag * direction[i] + lift * lift_direction[i] for i in range(3)
            )

        base = aero(flow)
        h, v = (float(self.controls[i]) * self.scalar_fins[i] for i in range(2))
        size = math.hypot(h, v)
        if dynamic * size > self.control_limit:
            scale = self.control_limit / (dynamic * size)
            h *= scale
            v *= scale
        if any(
            abs(float(self.controls[i])) * self.scalar_fins[i] > 1e-5 for i in range(2)
        ):
            control_flow = normalized(
                tuple(flow[i] + axes[2][i] * h + axes[1][i] * v for i in range(3)), nose
            )
            force = aero(control_flow)
        else:
            force = base
        body_y = dot(axes[1], force)
        body_z = dot(axes[2], force)
        moments = (
            float(torque_body[0]),
            float(torque_body[1]) - lever * body_z,
            float(torque_body[2]) + lever * body_y,
        )
        I = self.scalar_inertia
        gyro = (
            (I[2] - I[1]) * rate[1] * rate[2],
            (I[0] - I[2]) * rate[0] * rate[2],
            (I[1] - I[0]) * rate[0] * rate[1],
        )
        angular = []
        for i in range(3):
            damping = self.scalar_damping[i] * dynamic * rate[i]
            stop = I[i] * rate[i] / dt + moments[i]
            if abs(damping) > abs(stop):
                damping = -stop
            angular.append((gyro[i] + moments[i] + damping) / I[i])
        acc = [(float(force_world[i]) + base[i]) / mass for i in range(3)]
        if gravity:
            acc[1] -= 9.81
        length = math.sqrt(dot(acc, acc))
        if length >= 6000:
            acc = [x * 6000 / length for x in acc]
        return np.array(acc), np.array(angular)

# ---- compiled_fast.py ----
"""Optional compiled full-trajectory fast path; standard Python stays unchanged.

The readable C source is embedded by the single-file exporter. Linux/macOS/Windows need
a local C compiler once per source version. No downloaded binary or game ELF is
executed. Warm calls reuse code only; each trajectory is calculated from scratch.
"""
import copy
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import time

import numpy as np


_CF_SOURCE = r'''/* Approximate, per-tick 6DOF calculator. No game code, files, or syscalls.
 * ABI inputs are validated/owned contiguous float64 arrays in compiled_fast.py.
 * Only math is relaxed: binary64 intermediates and world-frame angle filtering.
 */
#include <math.h>
#include <stddef.h>
#include <string.h>

#if defined(_WIN32)
#define WT_EXPORT __declspec(dllexport)
#define WT_CALL __cdecl
#else
#define WT_EXPORT
#define WT_CALL
#endif

typedef struct {
  double x, y, z;
} V;
static V v(double x, double y, double z) { return (V){x, y, z}; }
static V add(V a, V b) { return v(a.x + b.x, a.y + b.y, a.z + b.z); }
static V sub(V a, V b) { return v(a.x - b.x, a.y - b.y, a.z - b.z); }
static V mul(V a, double k) { return v(a.x * k, a.y * k, a.z * k); }
static double dot(V a, V b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static double norm(V a) { return sqrt(dot(a, a)); }
static V cross(V a, V b) {
  return v(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
static V unit(V a, V fallback) {
  double n = norm(a);
  return n > 1e-9 ? mul(a, 1 / n) : fallback;
}
static double clip(double x, double lo, double hi) {
  return fmin(hi, fmax(lo, x));
}
static V readv(const double *a) { return v(a[0], a[1], a[2]); }
static void putv(double *a, V x) {
  a[0] = x.x;
  a[1] = x.y;
  a[2] = x.z;
}
static V f32(V a) { return v((float)a.x, (float)a.y, (float)a.z); }
static V mat(const double *r, V a) {
  return v(r[0] * a.x + r[1] * a.y + r[2] * a.z,
           r[3] * a.x + r[4] * a.y + r[5] * a.z,
           r[6] * a.x + r[7] * a.y + r[8] * a.z);
}
static V trans(const double *r, V a) {
  return v(r[0] * a.x + r[3] * a.y + r[6] * a.z,
           r[1] * a.x + r[4] * a.y + r[7] * a.z,
           r[2] * a.x + r[5] * a.y + r[8] * a.z);
}
static void rot(const double *q, double *r) {
  double x = q[0], y = q[1], z = q[2], w = q[3];
  double a[] = {1 - 2 * (y * y + z * z), 2 * (x * y - z * w),
                2 * (x * z + y * w),     2 * (x * y + z * w),
                1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                2 * (x * z - y * w),     2 * (y * z + x * w),
                1 - 2 * (x * x + y * y)};
  memcpy(r, a, sizeof(a));
}
static void attitude(double *q, V angle) {
  double x = -.5 * angle.x, y = -.5 * angle.y, z = -.5 * angle.z;
  double sx = sin(x), sy = sin(y), sz = sin(z), cx = cos(x), cy = cos(y),
         cz = cos(z);
  double X = cx * sy * sz + cy * cz * sx, Y = cx * cz * sy + cy * sx * sz,
         Z = cx * cy * sz - sy * sx * cz, W = cx * cy * cz - sy * sx * sz;
  double a = q[0], b = q[1], c = q[2], d = q[3];
  double t[] = {d * X + a * W + b * Z - c * Y, d * Y + b * W + c * X - a * Z,
                d * Z + c * W + a * Y - b * X, d * W - a * X - b * Y - c * Z};
  double n = sqrt(t[0] * t[0] + t[1] * t[1] + t[2] * t[2] + t[3] * t[3]);
  for (int i = 0; i < 4; i++)
    q[i] = (float)(t[i] / n);
}
/* Parameter indexes are generated from the Python-side registry. */
/* PARAMETERS */
/* OUTPUT_COLUMNS */
static double interp(const double *tables, int index, double x) {
  const double *a = tables + index * 129;
  int n = (int)a[0];
  if (x <= a[1])
    return a[2];
  for (int i = 1; i < n; i++) {
    double x0 = a[1 + 2 * (i - 1)], y0 = a[2 + 2 * (i - 1)], x1 = a[1 + 2 * i],
           y1 = a[2 + 2 * i];
    if (x <= x1)
      return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
  }
  return a[2 * n];
}
static void atmosphere(double h, double *rho, double *sound) {
  double hp = fmin(h, 18300), hd = fmax(h, 18300), h2 = hp * hp, h3 = h2 * hp,
         h4 = h3 * hp;
  *rho = fmax(0, 1.225 *
                     (1 - 9.59387e-5 * hp + 3.53118e-9 * h2 - 5.83556e-14 * h3 +
                      2.28719e-19 * h4) *
                     18300 / hd);
  *sound =
      20.1 * sqrt(fmax(1, 288.16 * (1 - 2.27712e-5 * hp + 2.18069e-10 * h2 -
                                    5.71104e-14 * h3 + 3.97306e-18 * h4)));
}
static double machfn(double m) {
  if (m < .61)
    return .308;
  if (m < 1)
    return .308 + .505 * pow(m - .61, 2.31);
  if (m < 1.4)
    return .551 + .4485 * pow(m - 1, .505) * exp(-5.68 * (m - 1));
  if (m < 4)
    return m / ((.356 * m + 2.237) * m - 1.4);
  return .302;
}
typedef struct {
  double mass, thrust, tvc;
  V force, torque;
} Motor;
static Motor engine(const double *p, const double *stages, int n, double age,
                    double h, double ch, double cv) {
  Motor m = {.mass = p[P_mass]};
  for (int i = 0; i < n; i++) {
    const double *s = stages + 7 * i;
    /* Branch timing is discrete: retain float32 motor clocks even in fast. */
    float local = (float)age - (float)s[0];
    float within = local - (float)s[1];
    m.mass -=
        local >= s[2]
            ? s[5]
            : (within > 0 ? (float)((within / (float)s[3]) * (float)s[5]) : 0);
    if (within > 0 && local < s[2]) {
      m.thrust += s[4];
      m.tvc += s[6];
    }
  }
  double mult = 1;
  if (p[P_pressure]) {
    double hp = fmin(h, 18300), hd = fmax(h, 18300);
    double pressure =
        ((((1.60373e-18 * hp - 1.3738e-13) * hp + 5.6763e-9) * hp -
          .000118441) *
             hp +
         1) *
        18300 / hd * 101300;
    double x0 = p[P_px0], x1 = p[P_px1], y0 = p[P_py0], y1 = p[P_py1];
    mult =
        pressure <= x0
            ? y0
            : (pressure >= x1 ? y1
                              : y0 + (y1 - y0) * (pressure - x0) / (x1 - x0));
  }
  double eff = m.thrust * mult, sh = ch * m.tvc, sv = cv * m.tvc;
  m.force = v(sqrt(fmax(0, 1 - sh * sh - sv * sv)) * eff, -sv * eff, -sh * eff);
  m.torque = v(0, -p[P_length] * .5 * m.force.z, p[P_length] * .5 * m.force.y);
  return m;
}
typedef struct {
  V pos, vel, rate, acc, feedback, request, gvel;
  double q[4], r[9], integral[2], derivative[2], error[2], controls[2], gain;
  int loft, relative;
} Missile;
typedef struct {
  V direction, omega;
  double range, range_derivative, closing, closing_derivative, last;
  int started, multipath;
} Observe;
static void channel(double *value, double *derivative, double measured,
                    double dt, const double *p, int offset) {
  double lo = p[offset], hi = p[offset + 1], half = .5 * p[offset + 2],
         k = fmin(48 * dt, 10);
  double alpha = 1 - pow(1 - p[offset + 3], k),
         beta = 1 - pow(1 - p[offset + 4], k * k);
  double prediction = dt * (*derivative) + *value,
         error = clip(measured, lo, hi) - prediction;
  *derivative = (float)(*derivative + beta * error / dt);
  *value = (float)clip(prediction + alpha * error, lo + half, hi - half);
}
static void observe(Observe *o, Missile *m, const double *p, double t, V target,
                    V tv) {
  if (p[P_radar]) {
    double h = fmax(target.y - p[P_ground], 0), lo = p[P_mp0], a = p[P_mp1],
           hi = p[P_mp2], b = p[P_mp3];
    if (h <= hi) {
      double factor = lo <= hi ? a : b;
      if (lo < h) {
        factor = b;
        if (h != hi && fabs(hi - lo) > 4e-19)
          factor = (b - a) * (h - lo) / (hi - lo) + a;
      }
      double old = target.y;
      target.y = (float)(target.y - 2 * h * factor);
      o->multipath += fabs(old - target.y) > 1e-6;
    }
  }
  V relative = sub(target, m->pos);
  double d = norm(relative);
  V direction = mul(relative, 1 / fmax(d, 1e-12));
  double closing = -dot(sub(tv, m->vel), direction);
  if (!o->started) {
    o->direction = f32(direction);
    o->omega = v(0, 0, 0);
    o->range = (float)d;
    o->range_derivative = (float)-closing;
    o->closing = (float)closing;
    o->closing_derivative = 0;
    o->started = 1;
  } else {
    double dt = t - o->last;
    V predicted = unit(
        add(o->direction, mul(cross(o->direction, o->omega), dt)), v(0, 0, 0));
    V innovation = cross(sub(direction, predicted), predicted);
    o->direction =
        unit(add(predicted, mul(cross(predicted, innovation), p[P_alpha])),
             v(0, 0, 0));
    o->omega = add(o->omega, mul(innovation, p[P_beta] / dt));
    if (p[P_range_valid])
      channel(&o->range, &o->range_derivative, d, dt, p, P_range_min);
    if (p[P_doppler_valid])
      channel(&o->closing, &o->closing_derivative, closing, dt, p,
              P_doppler_min);
  }
  if (!p[P_range_valid])
    o->range = d;
  if (!p[P_doppler_valid])
    o->closing = closing;
  o->last = t;
}
static void guidance(Missile *m, Observe *o, const double *p,
                     const double *tables, const double *stages, int nstages,
                     double age, double dt) {
  if (!p[P_locked] && o->range <= 1e-6) {
    m->request = v(0, 0, 0);
    m->controls[0] = m->controls[1] = 0;
    return;
  }
  m->relative = p[P_use_target_vel] && p[P_range_valid] && p[P_doppler_valid];
  m->gvel = m->relative ? add(mul(o->direction, o->closing),
                              mul(cross(o->omega, o->direction), o->range))
                        : m->vel;
  double tgo = fabs(o->closing) > 4e-19 ? o->range / o->closing : 0;
  m->gain = interp(tables, 0, age) * interp(tables, 1, tgo);
  m->controls[0] = m->controls[1] = 0;
  if (m->gain < .001) {
    m->request = v(0, 0, 0);
    return;
  }
  V effective = mul(o->omega, -m->gain * p[P_omega_gain]);
  if (p[P_angle_gain]) {
    V local = trans(m->r, o->direction);
    double pitch = atan2(local.y, hypot(local.x, local.z)),
           yaw = atan2(-local.z, local.x);
    effective.z += m->gain * p[P_angle_gain] * (pitch + p[P_angle_bias]);
    effective.y += m->gain * p[P_angle_gain] * yaw;
  }
  m->request = cross(effective, m->gvel);
  if (m->loft) {
    V u = o->direction;
    double tan_e = p[P_loft_tan];
    if (u.y * fabs(u.y) < (u.x * u.x + u.z * u.z) * tan_e * fabs(tan_e) ||
        dot(o->omega, o->omega) > p[P_loft_omega2])
      m->loft = 0;
    else {
      double pitch = atan2(m->r[3], hypot(m->r[0], m->r[6])) * 180 /
                     3.14159265358979323846;
      double elev = interp(tables, 6, o->range * o->range);
      m->request.y = fmax(m->request.y, p[P_loft_mult] * (elev - pitch));
    }
  }
  double frame[9];
  memcpy(frame, m->r, sizeof(frame));
  if (p[P_velocity_frame]) {
    V a = unit(v(m->r[0], m->r[3], m->r[6]), v(1, 0, 0)),
      b = unit(m->vel, v(m->r[0], m->r[3], m->r[6]));
    V c = cross(a, b);
    double d = dot(a, b), tr[9];
    if (d < -.9998999834060669) {
      V axis = unit(
          cross(a, fabs(a.z) <= .7071067811865476 ? v(0, 0, 1) : v(0, 1, 0)),
          v(0, 1, 0));
      double av[] = {axis.x, axis.y, axis.z};
      for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
          tr[3 * i + j] = 2 * av[i] * av[j] - (i == j);
    } else {
      double k[] = {0, -c.z, c.y, c.z, 0, -c.x, -c.y, c.x, 0};
      for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) {
          double kk = 0;
          for (int z = 0; z < 3; z++)
            kk += k[3 * i + z] * k[3 * z + j];
          tr[3 * i + j] = (i == j) + k[3 * i + j] + kk / (1 + d);
        }
    }
    for (int i = 0; i < 3; i++)
      for (int j = 0; j < 3; j++) {
        frame[3 * i + j] = 0;
        for (int z = 0; z < 3; z++)
          frame[3 * i + j] += m->r[3 * i + z] * tr[3 * z + j];
      }
  }
  V desired = trans(frame, m->request), feedback = trans(frame, m->feedback);
  double h = desired.z, vertical = desired.y, cap = p[P_req_cap], rho, sound;
  atmosphere(m->pos.y, &rho, &sound);
  double speed = norm(m->vel);
  if (p[P_limit_aoa]) {
    Motor eng = engine(p, stages, nstages, age, m->pos.y, 0, 0);
    double available =
        (p[P_cy] * p[P_lift_area] * .5 * speed * speed * rho + eng.thrust) *
        p[P_aoa_max] / eng.mass;
    cap = fmin(cap, available);
  }
  double size = hypot(h, vertical);
  if (size > cap) {
    h *= cap / size;
    vertical *= cap / size;
  }
  double scale2 = p[P_base_speed] > 0
                      ? (speed > 1e-19 ? pow(1.225 * p[P_base_speed], 2) /
                                             (rho * rho * speed * speed)
                                       : 0)
                      : 1,
         scale = sqrt(scale2);
  double kp = interp(tables, 2, age) * scale,
         ki = interp(tables, 3, age) * scale,
         kd = interp(tables, 4, age) * scale2, limit = interp(tables, 5, age),
         errors[] = {h - feedback.z, vertical - feedback.y};
  for (int i = 0; i < 2; i++) {
    double e = (float)errors[i];
    m->integral[i] = (float)clip(m->integral[i] + dt * e * ki, -limit, limit);
    m->derivative[i] =
        (float)((e - (dt * m->derivative[i] + m->error[i])) * 48 +
                m->derivative[i]);
    m->error[i] = e;
    m->controls[i] =
        (float)clip(e * kp + m->derivative[i] * kd + m->integral[i], -1, 1);
  }
}
static V aero(V direction, V nose, const double *p, double dynamic, double fm,
              double induced) {
  double cosine = dot(nose, direction), sin2 = fmax(0, 1 - cosine * cosine),
         cy = p[P_cy] * sqrt(sin2);
  if (cy > p[P_cy_peak])
    cy = fmax(0, 2 * p[P_cy_peak] - cy);
  double lift = cy * dynamic * p[P_lift_area] * (cosine >= 0 ? -1 : 1),
         drag = dynamic * p[P_drag_area] * p[P_cx] * (fm + induced * sin2);
  return add(mul(direction, -drag),
             mul(unit(sub(mul(direction, cosine), nose), v(0, 0, 0)), lift));
}
static void body(Missile *m, const double *p, double dt, Motor eng, V wind) {
  V nose = unit(v(m->r[0], m->r[3], m->r[6]), v(1, 0, 0)),
    air = sub(m->vel, wind);
  double speed = norm(air), rho, sound;
  atmosphere(m->pos.y, &rho, &sound);
  double dynamic = .5 * rho * speed * speed;
  V local = add(
        air, mat(m->r, v(0, p[P_lever] * m->rate.z, -p[P_lever] * m->rate.y))),
    flow = unit(local, unit(air, nose));
  double fm = machfn(speed / sound),
         induced = p[P_cx_aoa] *
                   (p[P_cxi_mach] ? 3.247 * (.308 + .75 * (fm - .308)) : 1);
  V base = aero(flow, nose, p, dynamic, fm, induced), torque_force = base;
  double h = m->controls[0] * p[P_fins_h],
         vertical = m->controls[1] * p[P_fins_v], size = hypot(h, vertical);
  if (dynamic * size > p[P_control_limit]) {
    double scale = p[P_control_limit] / (dynamic * size);
    h *= scale;
    vertical *= scale;
  }
  if (fabs(m->controls[0]) * p[P_fins_h] > 1e-5 ||
      fabs(m->controls[1]) * p[P_fins_v] > 1e-5) {
    V cf = add(flow, add(mul(v(m->r[2], m->r[5], m->r[8]), h),
                         mul(v(m->r[1], m->r[4], m->r[7]), vertical)));
    torque_force = aero(unit(cf, nose), nose, p, dynamic, fm, induced);
  }
  V bf = trans(m->r, torque_force),
    moment = add(eng.torque, v(0, -p[P_lever] * bf.z, p[P_lever] * bf.y));
  double rates[] = {m->rate.x, m->rate.y, m->rate.z},
         moments[] = {moment.x, moment.y, moment.z}, angular[3];
  V inertia = v(p[P_ix], p[P_iy], p[P_iz]),
    gyro = cross(m->rate, v(inertia.x * m->rate.x, inertia.y * m->rate.y,
                            inertia.z * m->rate.z));
  double gyros[] = {gyro.x, gyro.y, gyro.z};
  for (int i = 0; i < 3; i++) {
    double damping = p[P_damp_x + i] * dynamic * rates[i],
           stop = p[P_ix + i] * rates[i] / dt + moments[i];
    if (fabs(damping) > fabs(stop))
      damping = -stop;
    angular[i] = (gyros[i] + moments[i] + damping) / p[P_ix + i];
  }
  V acc = mul(add(mat(m->r, eng.force), base), 1 / eng.mass);
  acc.y -= 9.81;
  double length = norm(acc);
  if (length >= 6000)
    acc = mul(acc, 6000 / length);
  V oldvel = m->vel, ang = readv(angular);
  double d = (float)dt;
  m->pos = f32(add(m->pos, add(mul(m->vel, d), mul(acc, .5 * d * d))));
  m->vel = f32(add(m->vel, mul(acc, d)));
  attitude(m->q, add(mul(m->rate, dt), mul(ang, .5 * dt * dt)));
  m->rate = f32(add(m->rate, mul(ang, d)));
  double cap = p[P_end_speed], velnorm = norm(m->vel);
  if (fabs(cap) > 4e-19 && velnorm > fabs(cap))
    m->vel = f32(mul(m->vel, cap / velnorm));
  m->feedback = f32(mul(sub(m->vel, oldvel), (float)(1 / (float)dt)));
  m->acc = acc;
}
static double sphere(V r0, V r1, double radius, double start) {
  V delta = sub(r1, r0), x = add(r0, mul(delta, start));
  if (dot(x, x) <= radius * radius)
    return start;
  double a = dot(delta, delta);
  if (a == 0)
    return 2;
  double b = dot(r0, delta), c = dot(r0, r0) - radius * radius,
         disc = b * b - a * c;
  if (disc < 0)
    return 2;
  double f = (-b - sqrt(fmax(0, disc))) / a;
  return f >= start && f <= 1 ? f : 2;
}
static int event(double t, double dt, V a, V b, V ta, V tb, const double *p,
                 double *fraction) {
  V r0 = sub(ta, a), r1 = sub(tb, b);
  double f = 2;
  int code = 0;
  if (p[P_collision] > 0) {
    double x = sphere(r0, r1, p[P_collision], 0);
    if (x < f) {
      f = x;
      code = 1;
    }
  }
  if (a.y <= p[P_ground]) {
    if (0 < f) {
      f = 0;
      code = 3;
    }
  } else if (b.y <= p[P_ground]) {
    double x = (a.y - p[P_ground]) / (a.y - b.y);
    if (x < f) {
      f = x;
      code = 3;
    }
  }
  if (p[P_fuse] > 0 && t + dt >= p[P_fuse_arm]) {
    double x = sphere(r0, r1, p[P_fuse], fmax(0, (p[P_fuse_arm] - t) / dt));
    if (x < f) {
      f = x;
      code = 2;
    }
  }
  *fraction = code ? f : 1;
  return code;
}
static void record(double *row, double t, const Missile *m, const Observe *o,
                   const double *p, const double *target, const double *obs,
                   Motor eng, double path, int interpolated) {
  V tp = readv(target), tv = readv(target + 3), relative = sub(tp, m->pos);
  double range = norm(relative), speed = norm(m->vel),
         closing = range > 1e-9 ? -dot(relative, sub(tv, m->vel)) / range : NAN;
  row[O_time_s] = t;
  row[O_missile_age_s] = p[P_age] + t;
  putv(row + O_missile_x_m, m->pos);
  putv(row + O_missile_vx_m_s, m->vel);
  for (int j = 0; j < 4; j++)
    row[O_qx + j] = m->q[j];
  putv(row + O_omega_x_rad_s, m->rate);
  putv(row + O_target_x_m, tp);
  putv(row + O_target_vx_m_s, tv);
  row[O_range_m] = range;
  row[O_speed_m_s] = speed;
  row[O_mass_kg] = eng.mass;
  row[O_thrust_N] = eng.thrust;
  row[O_tvc_factor] = eng.tvc;
  row[O_speed_kmh] = speed * 3.6;
  row[O_target_speed_m_s] = norm(tv);
  row[O_air_speed_m_s] = norm(sub(m->vel, readv(p + P_wind_x)));
  row[O_closing_speed_m_s] = closing;
  row[O_linear_time_to_go_s] = closing > 1e-9 ? range / closing : NAN;
  row[O_specific_force_g] =
      t > 0 ? norm(add(m->acc, v(0, 9.81, 0))) / 9.81 : NAN;
  row[O_trajectory_normal_g] =
      t > 0 && speed > 1e-9
          ? norm(sub(m->acc,
                     mul(m->vel, dot(m->acc, m->vel) / (speed * speed)))) /
                9.81
          : NAN;
  row[O_acceleration_valid] = t > 0;
  row[O_distance_flown_m] = path;
  row[O_state_interpolated] = interpolated;
  putv(row + O_target_accel_x_m_s2, readv(target + 6));
  row[O_control_horizontal] = m->controls[0];
  row[O_control_vertical] = m->controls[1];
  row[O_requested_g] = norm(m->request) / 9.81;
  row[O_guidance_velocity_source] = m->relative;
  putv(row + O_guidance_vx_m_s, m->gvel);
  putv(row + O_feedback_accel_x_m_s2, m->feedback);
  row[O_observation_mode] = p[P_locked];
  row[O_locked] = 1;
  row[O_observation_time_s] = p[P_locked] ? o->last : t;
  row[O_source_observation_age_s] =
      p[P_locked] ? obs[6] + fmax(0, t - o->last) : 0;
  row[O_observed_range_m] = p[P_locked] ? o->range : range;
  row[O_observed_range_channel_valid] = p[P_locked] && p[P_range_valid];
  row[O_observed_doppler_channel_valid] = p[P_locked] && p[P_doppler_valid];
  putv(row + O_observed_los_x, p[P_locked] ? o->direction : v(NAN, NAN, NAN));
  putv(row + O_observed_omega_x_rad_s,
       p[P_locked] ? o->omega : v(NAN, NAN, NAN));
  putv(row + O_accel_x_m_s2, m->acc);
  /* 2026-10-02 新增输出列：mach / aoa_deg / aoa_eff_deg（定义见 docs/SOLVER-API.md §3.1）。
     行是**步后**状态，而 m->r 是**步前**姿态（body() 只就地更新 m->q），所以这里就地重算 R。
     攻角 = acos(...) 出来的**非负角** [0,180]；要带符号必须另立列名。
     `180.0 / pi` 必须"先算常量再乘"，才与 Python 的 math.degrees 逐位一致。 */
  double R[9], rho, sound, rad2deg = 180.0 / 3.14159265358979323846;
  rot(m->q, R);
  atmosphere(m->pos.y, &rho, &sound);
  V nose = unit(v(R[0], R[3], R[6]), v(1, 0, 0)),
    air = sub(m->vel, readv(p + P_wind_x)), air_hat = unit(air, nose);
  V flow = unit(
      add(air, mat(R, v(0, p[P_lever] * m->rate.z, -p[P_lever] * m->rate.y))),
      air_hat);
  row[O_mach] = norm(air) / sound;
  row[O_aoa_deg] = acos(clip(dot(nose, air_hat), -1, 1)) * rad2deg;
  row[O_aoa_eff_deg] = acos(clip(dot(nose, flow), -1, 1)) * rad2deg;
}
WT_EXPORT int WT_CALL wt_fast_abi(void) { return 1; }
WT_EXPORT int WT_CALL wt_fast_run(const double *p, const double *tables, const double *engines,
                int nengine, const double *initial, const double *times,
                const double *targets, const double *observations, int count,
                double *out, double *stats) {
  Missile m = {0};
  Observe o = {0};
  m.pos = f32(readv(initial));
  for (int j = 0; j < 4; j++)
    m.q[j] = (float)initial[3 + j];
  m.vel = f32(readv(initial + 7));
  m.rate = f32(readv(initial + 10));
  m.feedback = readv(initial + 13);
  m.acc = m.feedback;
  m.gvel = readv(initial + 7);
  m.loft = (int)p[P_loft_enabled];
  rot(m.q, m.r);
  if (p[P_locked])
    observe(&o, &m, p, 0, f32(readv(observations)), readv(observations + 3));
  Motor eng = engine(p, engines, nengine, p[P_age], m.pos.y, 0, 0);
  double minimum = norm(sub(readv(targets), m.pos)), closest = 0, path = 0,
         peak_speed = norm(m.vel), peak_request = 0, peak_specific = 0,
         peak_normal = 0, fraction;
  int terminal = event(0, 1e-9, m.pos, m.pos, readv(targets), readv(targets), p,
                       &fraction),
      rows = 1, steps = 0;
  record(out, 0, &m, &o, p, targets, observations, eng, 0, 0);
  for (int i = 0; i < count - 1 && !terminal; i++) {
    double t = times[i], dt = times[i + 1] - t;
    const double *target = targets + 9 * i, *next = target + 9,
                 *obs = observations + 9 * i;
    if (p[P_locked]) {
      if (i)
        observe(&o, &m, p, t, f32(readv(obs)), readv(obs + 3));
    } else {
      V r = sub(f32(readv(target)), m.pos),
        dv = sub(f32(readv(target + 3)), m.vel);
      o.range = norm(r);
      o.direction = mul(r, 1 / fmax(o.range, 1e-12));
      o.omega = mul(cross(r, dv), -1 / fmax(o.range * o.range, 1e-24));
      o.closing = -dot(r, dv) / fmax(o.range, 1e-12);
    }
    rot(m.q, m.r);
    guidance(&m, &o, p, tables, engines, nengine, p[P_age] + t, dt);
    Missile old = m;
    eng = engine(p, engines, nengine, p[P_age] + t + dt, m.pos.y, m.controls[0],
                 m.controls[1]);
    body(&m, p, dt, eng, readv(p + P_wind_x));
    if (!isfinite(norm(m.pos)) || !isfinite(norm(m.vel)) ||
        !isfinite(norm(m.rate)))
      return -1;
    terminal =
        event(t, dt, old.pos, m.pos, readv(target), readv(next), p, &fraction);
    V r0 = sub(readv(target), old.pos), r1 = sub(readv(next), m.pos),
      delta = sub(r1, r0);
    double d2 = dot(delta, delta),
           cp = d2 ? clip(-dot(r0, delta) / d2, 0, fraction) : 0,
           cpa = norm(add(r0, mul(delta, cp)));
    if (cpa < minimum) {
      minimum = cpa;
      closest = t + cp * dt;
    }
    path += norm(sub(m.pos, old.pos)) * fraction;
    double final_target[9];
    memcpy(final_target, next, sizeof(final_target));
    double integrated_height = m.pos.y;
    if (terminal) {
      m.pos = add(old.pos, mul(sub(m.pos, old.pos), fraction));
      m.vel = add(old.vel, mul(sub(m.vel, old.vel), fraction));
      m.rate = add(old.rate, mul(sub(m.rate, old.rate), fraction));
      double qnorm = 0;
      for (int j = 0; j < 4; j++) {
        m.q[j] = old.q[j] + fraction * (m.q[j] - old.q[j]);
        qnorm += m.q[j] * m.q[j];
      }
      for (int j = 0; j < 4; j++)
        m.q[j] /= sqrt(qnorm);
      for (int j = 0; j < 6; j++)
        final_target[j] = target[j] + fraction * (next[j] - target[j]);
      eng = engine(p, engines, nengine, p[P_age] + t + fraction * dt,
                   integrated_height, m.controls[0], m.controls[1]);
    }
    double speed = norm(m.vel);
    peak_speed = fmax(peak_speed, speed);
    peak_request = fmax(peak_request, norm(m.request) / 9.81);
    peak_specific = fmax(peak_specific, norm(add(m.acc, v(0, 9.81, 0))) / 9.81);
    if (speed > 1e-6) {
      V vh = mul(m.vel, 1 / speed);
      peak_normal =
          fmax(peak_normal, norm(sub(m.acc, mul(vh, dot(m.acc, vh)))) / 9.81);
    }
    steps++;
    record(out + rows * O_COUNT, t + fraction * dt, &m, &o, p, final_target,
           obs, eng, path, terminal && fraction < 1);
    rows++;
  }
  stats[0] = terminal;
  stats[1] = rows;
  stats[2] = minimum;
  stats[3] = closest;
  stats[4] = path;
  stats[5] = peak_speed;
  stats[6] = peak_request;
  stats[7] = peak_specific;
  stats[8] = peak_normal;
  stats[9] = o.multipath;
  stats[10] = steps;
  return 0;
}
'''
_CF_PARAMETERS = '''mass length pressure px0 py0 px1 py1 radar ground mp0 mp1 mp2 mp3
alpha beta range_valid doppler_valid range_min range_max range_width range_alpha range_beta
doppler_min doppler_max doppler_width doppler_alpha doppler_beta use_target_vel omega_gain angle_gain angle_bias
loft_tan loft_omega2 loft_mult velocity_frame req_cap limit_aoa cy cy_peak lift_area aoa_max base_speed
drag_area cx cx_aoa cxi_mach lever fins_h fins_v control_limit ix iy iz damp_x damp_y damp_z end_speed
age loft_enabled collision fuse fuse_arm wind_x wind_y wind_z locked'''.split()
_CF_COLUMNS = '''time_s missile_age_s missile_x_m missile_y_m missile_z_m missile_vx_m_s missile_vy_m_s missile_vz_m_s
qx qy qz qw omega_x_rad_s omega_y_rad_s omega_z_rad_s target_x_m target_y_m target_z_m
target_vx_m_s target_vy_m_s target_vz_m_s range_m speed_m_s mass_kg thrust_N tvc_factor speed_kmh
target_speed_m_s air_speed_m_s closing_speed_m_s linear_time_to_go_s specific_force_g trajectory_normal_g
acceleration_valid distance_flown_m state_interpolated target_accel_x_m_s2 target_accel_y_m_s2 target_accel_z_m_s2
control_horizontal control_vertical requested_g guidance_velocity_source guidance_vx_m_s guidance_vy_m_s guidance_vz_m_s
feedback_accel_x_m_s2 feedback_accel_y_m_s2 feedback_accel_z_m_s2 observation_mode locked observation_time_s
source_observation_age_s observed_range_m observed_range_channel_valid observed_doppler_channel_valid
observed_los_x observed_los_y observed_los_z observed_omega_x_rad_s observed_omega_y_rad_s observed_omega_z_rad_s
accel_x_m_s2 accel_y_m_s2 accel_z_m_s2
mach aoa_deg aoa_eff_deg'''.split()
_CF_CODE = _CF_SOURCE.replace('/* PARAMETERS */', 'enum {' + ','.join('P_'+s for s in _CF_PARAMETERS) + '};').replace(
    '/* OUTPUT_COLUMNS */', 'enum {' + ','.join('O_'+s for s in _CF_COLUMNS) + ',O_COUNT};')
_CF_FLAGS = ('-O3', '-std=c11', '-fPIC', '-shared', '-ffp-contract=off')
_CF_WINDOWS_FLAGS = ('-O3', '-std=c11', '-shared', '-ffp-contract=off', '-static-libgcc')
_CF_MSVC_FLAGS = ('/nologo', '/O2', '/std:c11', '/fp:precise', '/LD', '/MT', '/TC')
_CF_HASH = hashlib.sha256((_CF_CODE + repr((_CF_FLAGS, _CF_WINDOWS_FLAGS, _CF_MSVC_FLAGS))
                         + platform.system() + platform.machine() + str(ctypes.sizeof(ctypes.c_void_p))).encode()).hexdigest()
_CF_LIBRARY = None
_CF_LOCK = threading.Lock()


class CompiledFastUnavailable(RuntimeError):
    """A known unsupported environment/input uses the existing Python fast path."""


def _cf_cache_directory():
    override = os.environ.get('WT_MISSILE_FAST_CACHE')
    if override:
        return Path(override).expanduser().resolve()
    if platform.system() == 'Windows':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))
    return (base/'wt-missile'/'compiled-fast').resolve()


@contextmanager
def _cf_file_lock(path):
    with path.open('a+b') as lock:
        if platform.system() == 'Windows':
            import msvcrt
            # Use Win32 error codes directly; some CRT implementations lose
            # the lock-contention error when msvcrt.locking maps it to errno.
            class Overlapped(ctypes.Structure):
                _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                            ('offset', ctypes.c_uint32), ('offset_high', ctypes.c_uint32),
                            ('event', ctypes.c_void_p)]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            pointer = ctypes.POINTER(Overlapped)
            kernel.LockFileEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                          ctypes.c_uint32, ctypes.c_uint32, pointer]
            kernel.UnlockFileEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                            ctypes.c_uint32, pointer]
            kernel.LockFileEx.restype = kernel.UnlockFileEx.restype = ctypes.c_int
            handle = msvcrt.get_osfhandle(lock.fileno())
            state = Overlapped()
            deadline = time.monotonic() + 90
            while not kernel.LockFileEx(handle, 3, 0, 1, 0, ctypes.byref(state)):
                error = ctypes.get_last_error()
                if error != 33:  # ERROR_LOCK_VIOLATION
                    raise ctypes.WinError(error)
                if time.monotonic() >= deadline:
                    raise CompiledFastUnavailable('Timed out waiting for compiled fast cache lock')
                time.sleep(.05)
            try:
                yield
            finally:
                if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(state)):
                    raise ctypes.WinError(ctypes.get_last_error())
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _cf_build_command(source, output):
    windows = platform.system() == 'Windows'
    names = [os.environ['CC']] if os.environ.get('CC') else (['gcc', 'cl', 'clang-cl', 'clang', 'cc'] if windows else ['cc'])
    compiler = next((found for name in names if (found := shutil.which(name))), None)
    if compiler is None:
        raise CompiledFastUnavailable('No C compiler/cache available; install MinGW-w64 or use a Visual Studio developer terminal on Windows')
    name = Path(compiler).stem.lower()
    if windows and name in ('cl', 'clang-cl'):
        return [compiler, *_CF_MSVC_FLAGS, str(source), '/link', '/OUT:'+str(output)]
    flags = _CF_WINDOWS_FLAGS if windows else _CF_FLAGS
    return [compiler, *flags, str(source), '-o', str(output), '-lm']


def _cf_library():
    global _CF_LIBRARY
    if os.environ.get('WT_MISSILE_COMPILED_FAST', '1') == '0':
        raise CompiledFastUnavailable('Compiled fast disabled by WT_MISSILE_COMPILED_FAST=0')
    if _CF_LIBRARY is not None:
        return _CF_LIBRARY
    if platform.system() not in ('Linux', 'Darwin', 'Windows'):
        raise CompiledFastUnavailable('Compiled fast supports Linux/macOS/Windows; using Python fast')
    with _CF_LOCK:
        if _CF_LIBRARY is not None:
            return _CF_LIBRARY
        try:
            cache = _cf_cache_directory()
            cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            extension = '.dll' if platform.system() == 'Windows' else '.so'
            library_path = cache / (_CF_HASH + extension)
            hash_path = cache / (_CF_HASH + '.sha256')
            with _cf_file_lock(cache / (_CF_HASH + '.lock')):
                valid = library_path.is_file() and hash_path.is_file() and hashlib.sha256(library_path.read_bytes()).hexdigest() == hash_path.read_text().strip()
                if not valid:
                    with tempfile.TemporaryDirectory(prefix='build-', dir=cache) as temp:
                        source, output = Path(temp)/'kernel.c', Path(temp)/('kernel'+extension)
                        source.write_text(_CF_CODE, encoding='utf-8')
                        # MinGW's linker may use ANSI argv even on Unicode-aware
                        # Python. Keep compiler arguments ASCII; cwd is set via
                        # the Windows wide-character process API.
                        command = _cf_build_command(Path(source.name), Path(output.name))
                        built = subprocess.run(command, cwd=temp, capture_output=True, text=True, errors='replace', timeout=60,
                                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if platform.system() == 'Windows' else 0)
                        if built.returncode:
                            raise CompiledFastUnavailable('C compiler failed: ' + (built.stdout+'\n'+built.stderr)[-1200:])
                        digest = hashlib.sha256(output.read_bytes()).hexdigest()
                        temporary_hash = Path(temp)/'kernel.sha256'
                        temporary_hash.write_text(digest+'\n')
                        output.chmod(0o700)
                        os.replace(output, library_path)
                        os.replace(temporary_hash, hash_path)
                library = ctypes.CDLL(str(library_path.resolve()))
                if library.wt_fast_abi() != 1:
                    raise CompiledFastUnavailable('Compiled fast ABI mismatch')
                ptr = ctypes.POINTER(ctypes.c_double)
                library.wt_fast_run.argtypes = [ptr, ptr, ptr, ctypes.c_int, ptr, ptr, ptr, ptr, ctypes.c_int, ptr, ptr]
                library.wt_fast_run.restype = ctypes.c_int
                _CF_LIBRARY = library
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CompiledFastUnavailable('Compiled fast unavailable: '+str(exc)) from exc
    return _CF_LIBRARY


def _cf_config(s, p):
    body, controller = Body(p), Controller(p)
    engine = Engine(p, s['launch'].get('motor_start_times_s'))
    a, g = p['guidance']['guidanceAutopilot'], p['guidance']
    radar = 'radarSeeker' in g
    seeker = g['radarSeeker' if radar else 'opticalSeeker']
    curve = seeker.get('multipathEffect', (0, 0, 0, 0))
    if isinstance(curve, list):
        if not curve or any(x != curve[0] for x in curve):
            raise ValueError('Conflicting duplicate multipath configuration')
        curve = curve[0]
    pressure_curve = engine.pressure_curve or (0, 1, 1, 1)
    fuse = p.get('proximityFuse', {})
    values = dict(mass=p['mass'], length=p['length'], pressure=engine.pressure_curve is not None,
                  px0=pressure_curve[0], py0=pressure_curve[1], px1=pressure_curve[2], py1=pressure_curve[3],
                  radar=radar, ground=s['ground_height_m'], alpha=seeker.get('filterAlpha', .85), beta=seeker.get('filterBetta', .2),
                  use_target_vel=g.get('useTargetVel', False), omega_gain=0 if a.get('purePursuit') else a.get('omegaToAccelMult', a.get('propNavMult', 4)),
                  angle_gain=a.get('propNavMult', 4) if a.get('purePursuit') else a.get('angleToAccelMult', 0),
                  angle_bias=math.radians(a.get('angleBias', 0)), loft_tan=math.tan(math.radians(a.get('loftTargetElevation', -89.9))),
                  loft_omega2=math.radians(a.get('loftTargetOmegaMax', 0))**2, loft_mult=a.get('loftAngleToAccelMult', 0),
                  velocity_frame=a.get('velFrameReference', True), req_cap=a.get('reqAccelMax', a.get('propNavAccelMax', 1000))*9.81,
                  limit_aoa=a.get('limitAoa', False), cy=body.cy, lift_area=body.lift_area,
                  aoa_max=min(math.radians(a.get('aoaMax', 0)), p.get('CyMaxAoA', 1)), base_speed=a.get('baseIndSpeed', 0)/3.6,
                  drag_area=body.drag_area, cx=p['CxK'], cx_aoa=body.cx_aoa, cxi_mach=p.get('useCxiMach', True),
                  lever=body.lever, fins_h=body.fins[0], fins_v=body.fins[1], control_limit=body.control_limit,
                  ix=body.inertia[0], iy=body.inertia[1], iz=body.inertia[2], damp_x=body.damping[0], damp_y=body.damping[1], damp_z=body.damping[2],
                  end_speed=p.get('endSpeed', 0), age=s['launch']['age_s'], loft_enabled=a.get('loftEnabled', False),
                  collision=s['target']['collision_radius_m'], fuse=fuse.get('radius', 0) if p.get('hasProximityFuse') and s.get('proximity_fuse', True) else 0,
                  fuse_arm=max(0, fuse.get('timeOut', 0)-s['launch']['age_s']), wind_x=s['wind_m_s'][0], wind_y=s['wind_m_s'][1], wind_z=s['wind_m_s'][2],
                  locked=s['observation']['mode']=='locked')
    values.update({'mp'+str(i): x for i, x in enumerate(curve)})
    for name, channel_name, gate_name in [('range', 'distance', 'distGate'), ('doppler', 'dopplerSpeed', 'dopplerSpeedGate')]:
        valid = radar and seeker.get(channel_name, {}).get('presents', False)
        channel, gate = (seeker[channel_name], seeker[gate_name]) if valid else ({}, {})
        values.update({name+'_valid': valid, name+'_min': channel.get('minValue', 0), name+'_max': channel.get('maxValue', 1e9),
                       name+'_width': channel.get('width', 0), name+'_alpha': gate.get('filterAlpha', 1), name+'_beta': gate.get('filterBetta', 0)})
    values['cy_peak'] = body.cy_peak
    params = np.array([values[k] for k in _CF_PARAMETERS], dtype=np.float64)
    tables = np.zeros((7, 129), dtype=np.float64)
    pid = controller.pid_tables if controller.schedule else tuple(((0, a.get('accelControl'+k, d)),) for k, d in [('Prop', .001), ('Intg', 0), ('Diff', 0), ('IntgLim', 1)])
    loft = [(0, a.get('loftElevation', 0))]
    if 'rangeToLoftElevation' in a:
        r0, e0, r1, e1 = a['rangeToLoftElevation']
        loft = sorted([(r0*r0, e0), (r1*r1, e1)])
        if r0*r0 == r1*r1:
            raise CompiledFastUnavailable('Coincident loft interpolation knots use Python fast')
    for dest, table in zip(tables, (controller.age_gain, controller.hit_gain, *pid, loft)):
        if not 1 <= len(table) <= 64:
            raise CompiledFastUnavailable('Table length outside compiled fast bounds')
        dest[0] = len(table)
        dest[1:1+2*len(table)] = np.asarray(table).ravel()
    stages = []
    for start, rows, _ in engine.prepared_motors:
        for begin, end, prior, duration, thrust, fuel, tvc in rows:
            stages.append((start, begin, end, duration, thrust, fuel, tvc))
    return params, tables, np.array(stages, dtype=np.float64), engine


def compiled_simulate(s, scene_loop, missile_factory):
    # Only fixed physics grids are accelerated initially. Other inputs retain
    # their existing solver and explicitly report the fallback, never drop data.
    if s['sample_period_s'] is not None:
        raise CompiledFastUnavailable('Periodic output uses Python fast; per-step output enables compiled fast')
    if s['dt_s'] != CLIENT_OBJECT_DT_S and s.get('step_policy') != 'fixed':
        raise CompiledFastUnavailable('Boundary-split physics grid uses Python fast')
    library = _cf_library()
    p, identity = load_profile(s['missile'], s['version'])
    # Instantiation preserves the same unsupported-profile checks as standard.
    launch = s['launch']
    velocity = np.array(launch['velocity_m_s']) + rotation(launch['quaternion_xyzw']) @ launch['ejection_velocity_body_m_s']
    missile_factory(s['version'])(p, launch['position_m'], velocity, launch['quaternion_xyzw'], launch['angular_rate_rad_s'])
    remaining = float(p['timeLife'])-launch['age_s']
    if remaining <= 0:
        raise ValueError('Initial missile age already reaches resource lifetime')
    horizon = min(s['duration_s'], remaining)
    count = int(math.ceil(horizon/s['dt_s']))+1
    if count > 200001:
        raise CompiledFastUnavailable('More than 200000 steps uses the Python fast path')
    times = np.minimum(np.arange(count, dtype=np.float64)*s['dt_s'], horizon)
    # Match the scene loop's horizon tolerance and avoid a vanishing last step.
    if count > 2 and horizon-times[-2] <= 1e-10:
        times = times[:-1]
    count = len(times)
    params, tables, stages, engine = _cf_config(s, p)
    initial = np.r_[launch['position_m'], launch['quaternion_xyzw'], velocity,
                    launch['angular_rate_rad_s'], launch['feedback_acceleration_m_s2']].astype(np.float64)
    targets = np.empty((count, 9), dtype=np.float64)
    observations = np.zeros((count, 9), dtype=np.float64)
    target = Target(s['target'])
    target.advance(0)
    transport = ObservationTransport(s['observation'], target.position, target.velocity)
    locked = s['observation']['mode'] == 'locked'
    for i, at in enumerate(times):
        target.advance(float(at))
        targets[i, :3], targets[i, 3:6], targets[i, 6:] = target.position, target.velocity, target.acceleration
        if locked:
            pos, vel, age = transport.update(float(at), target.position, target.velocity)
            observations[i, :3], observations[i, 3:6] = pos, vel
            observations[i, 6:] = age, transport.count, transport.received
    output = np.empty((count, len(_CF_COLUMNS)), dtype=np.float64)
    stats = np.zeros(11, dtype=np.float64)
    ptr = lambda a: a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    code = library.wt_fast_run(ptr(params), ptr(tables), ptr(stages), len(stages), ptr(initial), ptr(times), ptr(targets), ptr(observations), count, ptr(output), ptr(stats))
    if code:
        raise RuntimeError('Nonfinite compiled fast state')
    output = output[:int(stats[1])]
    nullable = ['closing_speed_m_s', 'linear_time_to_go_s', 'specific_force_g', 'trajectory_normal_g',
                'observed_los_x', 'observed_los_y', 'observed_los_z', 'observed_omega_x_rad_s', 'observed_omega_y_rad_s', 'observed_omega_z_rad_s']
    finite = np.isfinite(output)
    for key in nullable:
        # Only NaN is an absent measurement; infinity is always an error.
        i = _CF_COLUMNS.index(key)
        finite[:, i] |= np.isnan(output[:, i])
    if not finite.all() or not np.isfinite(stats).all():
        raise RuntimeError('Nonfinite compiled fast output')
    # Reuse the shared summary contract through one tiny STANDARD calculation.
    # It does not approximate or seed the compiled state; that state starts fresh.
    mini = copy.deepcopy(s)
    mini.update(fastmode=False, duration_s=min(.001, horizon))
    _, summary, _ = scene_loop(mini, missile_factory(s['version']), python_kernel=True, _validated=True)
    rows = [dict(zip(_CF_COLUMNS, values)) for values in output.tolist()]
    boolean = ['acceleration_valid', 'state_interpolated', 'locked', 'observed_range_channel_valid', 'observed_doppler_channel_valid']
    for row in rows:
        row['guidance_velocity_source'] = 'radar_relative_velocity' if row['guidance_velocity_source'] else 'missile_velocity'
        row['observation_mode'] = s['observation']['mode']
        for key in boolean:
            row[key] = bool(row[key])
        for key in nullable:
            if math.isnan(row[key]):
                row[key] = None
    end, steps = rows[-1], int(stats[10])
    event = {1: 'contact', 2: 'proximity_fuse', 3: 'ground'}.get(int(stats[0]), 'lifetime' if remaining <= s['duration_s'] else 'time_limit')
    if end['state_interpolated']:
        exact_target = Target(s['target'])
        exact_target.advance(end['time_s'])
        for i, axis in enumerate('xyz'):
            end['target_accel_'+axis+'_m_s2'] = float(exact_target.acceleration[i])
    summary.update(model='python-game-6dof-'+s['version']+'-compiled-fast', terminal_event=event,
                   time_s=end['time_s'], steps=steps, minimum_separation_until_event_m=float(stats[2]), closest_time_s=float(stats[3]),
                   distance_flown_m=float(stats[4]), final_speed_m_s=end['speed_m_s'], peak_speed_m_s=float(stats[5]),
                   peak_requested_g_before_native_limit=float(stats[6]), peak_specific_force_g=float(stats[7]), peak_trajectory_normal_g=float(stats[8]))
    summary['fastmode'].update(enabled=True, backend='compiled-c', compiled_kernel_sha256=_CF_HASH,
                               fallback_reason=None, math_acceleration_active=True,
                               approximation='Compiled per-tick 6DOF; binary64 intermediates, float32 state boundaries, world-frame angle filter. No output decimation or enlarged physics steps. Not bit-identical to standard.')
    packet = observations[max(0, steps-1)]
    summary['observation'].update(filter_updates=max(1, steps) if locked else 0, multipath_updates=int(stats[9]),
                                  packets_captured=int(packet[7]) if locked else 0, packets_received=int(packet[8]) if locked else 0)
    return s, summary, rows

# ---- game_kernel.py ----
"""Independent Python implementation of the recovered missile computation.

The NativeMissile-compatible interface is solely for sharing the scene loop
and differential tests. This module never imports an emulator or an ELF.
"""

import numpy as np


MODEL = "python-game-6dof-v1"


def unsupported(params):
    guidance = params["guidance"]
    for key in (
        "orientationAutopilot",
        "orientationAutopilotLeadingMult",
        "propulsionAutopilot",
        "useThrustVectoring",
    ):
        if key in guidance:
            return f"Game outer controller {key} has not been ported; refusing an approximate replacement"
    if isinstance(params.get("endSpeed", 0), list):
        return "Duplicate endSpeed values require a verified BLK getter decision"
    if "Cy" in params and not isinstance(params["Cy"], (int, float)):
        return "Nonconstant aerodynamic coefficient tables are not supported"
    return None


def profile_support(name, version="2.59.0.28"):
    if version not in ("2.59.0.28", "2.59.0.22"):
        return "Unsupported resource version"
    try:
        params, _ = load_profile(name, version)
        reason = unsupported(params)
        if reason:
            return reason
        Engine(params)
    except (KeyError, ValueError, TypeError) as exc:
        return str(exc)
    return None


def missile_class(version="2.59.0.28"):
    if version not in ("2.59.0.28", "2.59.0.22"):
        raise ValueError("Unsupported resource version")

    class PythonMissile:
        def __init__(self, params, position, velocity, quaternion, rates=(0, 0, 0)):
            reason = unsupported(params)
            if reason:
                raise ValueError(reason)
            self.version = version
            self.params = params
            self.autopilot = params["guidance"]["guidanceAutopilot"]
            self._state = np.asarray(
                [*position, *quaternion, *velocity, *rates], dtype=np.float32
            ).astype(float)
            self.body = Body(params)
            self._rotation = None
            self.controller = Controller(params)
            self.setup_engine()
            self.lastacc = np.zeros(3)
            self.angular_acc = np.zeros(3)
            self.feedback_acceleration = np.zeros(3)
            self.request = np.zeros(3)
            self.guidance_velocity = np.asarray(velocity, dtype=float)
            self.guidance_velocity_source = "missile_velocity"
            self.fastmode = False

        def setup_engine(self, ignition_times=None):
            self.motor = Engine(self.params, ignition_times)
            self.engine_boundaries = self.motor.boundaries
            self.motor_start_times = self.motor.start_times

        def state(self):
            return self._state.copy()

        def body_rotation(self):
            # One pose per integration step, shared read-only by observation,
            # guidance, thrust and aerodynamics. Never shared between missiles.
            if self._rotation is None:
                self._rotation = rotation(self._state[3:7])
                self._rotation.flags.writeable = False
            return self._rotation

        def engine(self, age, controls=(0, 0)):
            return self.motor.evaluate(age, controls, self._state[1])

        def reference_velocity(self, observation):
            g = self.params["guidance"]
            radar = g.get("radarSeeker", {})
            valid = (
                g.get("useTargetVel", False)
                and observation.get(
                    "range_valid", radar.get("distance", {}).get("presents", False)
                )
                and observation.get(
                    "doppler_valid",
                    radar.get("dopplerSpeed", {}).get("presents", False),
                )
            )
            if not valid:
                self.guidance_velocity_source = "missile_velocity"
                return self._state[7:10].copy()
            self.guidance_velocity_source = "radar_relative_velocity"
            f = np.float32
            u = np.asarray(observation["direction"], dtype=f)
            omega = np.asarray(observation["omega"], dtype=f)
            return f(
                f(u * f(observation["closing_m_s"]))
                + f(cross3(omega, u) * f(observation["range_m"]))
            ).astype(float)

        def guide(self, age, dt, target_position, target_velocity, observation=None):
            if observation is None:
                relative = (
                    np.asarray(target_position, dtype=np.float32).astype(float)
                    - self._state[:3]
                )
                dv = (
                    np.asarray(target_velocity, dtype=np.float32).astype(float)
                    - self._state[7:10]
                )
                distance = float(norm64(relative))
                if distance <= 1e-6:
                    self.request = np.zeros(3)
                    return np.zeros(2)
                observation = {
                    "direction": relative / distance,
                    "omega": -cross3(relative, dv) / (distance * distance),
                    "range_m": distance,
                    "closing_m_s": -float(relative @ dv) / distance,
                }
            self.guidance_velocity = self.reference_velocity(observation)
            self.request = self.controller.request(
                age, self._state, observation, self.guidance_velocity,
                body_rotation=self.body_rotation(),
            )
            if self.controller.gain < 0.001:
                return np.zeros(2)
            mass, thrust = self.params["mass"], 0.0
            if self.autopilot.get("limitAoa", False):
                mass, thrust, *_ = self.engine(age)
            return self.controller.pid(
                age,
                dt,
                self.request,
                self._state,
                self.feedback_acceleration,
                engine_mass=mass,
                thrust=thrust,
                body_rotation=self.body_rotation(),
            )

        def step(
            self,
            age,
            dt,
            request,
            mass,
            force_world,
            torque_body=(0, 0, 0),
            wind=(0, 0, 0),
            controls=None,
            gravity=False,
        ):
            if controls is None:
                engine_mass, thrust, *_ = self.engine(age)
                controls = self.controller.pid(
                    age,
                    dt,
                    request,
                    self._state,
                    self.feedback_acceleration,
                    engine_mass=engine_mass,
                    thrust=thrust,
                    body_rotation=self.body_rotation(),
                )
            old_velocity = self._state[7:10].astype(np.float32)
            self._state, self.lastacc, self.angular_acc = self.body.step(
                self._state, dt, mass, force_world, torque_body, wind, controls, gravity,
                body_rotation=self.body_rotation(),
            )
            self._rotation = None
            # Recovered object-level 85db660 cap, applied after body integration.
            cap = self.params.get("endSpeed", 0)
            speed = float(norm64(self._state[7:10]))
            if abs(cap) > 4e-19 and speed > abs(cap):
                self._state[7:10] = (self._state[7:10] * (cap / speed)).astype(
                    np.float32
                )
            self.feedback_acceleration = (
                (self._state[7:10].astype(np.float32) - old_velocity)
                * np.float32(1 / np.float32(dt))
            ).astype(float)
            if not np.isfinite(self._state).all():
                raise RuntimeError("Nonfinite Python game state")
            return self.state(), np.asarray(controls)

    return PythonMissile


def python_simulate(raw, *, _validated=False):

    version = raw.get("version", "2.59.0.28")
    if raw.get('fastmode', False):
        s = raw if _validated else validate(raw)
        try:
            return compiled_simulate(s, scene_simulate, missile_class)
        except CompiledFastUnavailable as exc:
            normalized, summary, rows = scene_simulate(s, missile_class(version), python_kernel=True, _validated=True)
            summary['fastmode']['backend'] = 'python-legacy'
            summary['fastmode']['compiled_fallback_reason'] = str(exc)
            return normalized, summary, rows
    return scene_simulate(raw, missile_class(version), python_kernel=True, _validated=_validated)

# ---- core.py ----
"""Native guidance -> engine -> physics, with explicit offline world adapters."""
import math
import numpy as np

BOUNDARIES = [
    'Native 2.59.0.28 guidance/PID, engine stages/TVC, atmosphere, aerodynamic force/torque and integration; synthetic state/config construction.',
    'Permanently assigned target; unlimited acquisition, tracking rate and off-boresight angle. Missile autopilot, aerodynamic and actuator limits remain active.',
    'Single point target with no competing returns; native echo weighting, random measurement noise and carrier radar/network scheduling are not reconstructed.',
    'Outer scheduling uses a version-scoped client default or an explicit step, with parameter/event boundary splits; full object/server scheduling is not reproduced.',
    'Launch velocity is the supplied world velocity plus explicit body ejection velocity; rack/launcher transforms are supplied, not reconstructed.',
    'Target collision is a user-sized sphere, proximity uses resource radius/timeout; detailed game fuse gates, mesh collision and terrain are not reproduced.',
    'Flat ground, standard atmosphere, no environment modifiers or random propulsion dispersion; no damage/kill model; captured-trajectory comparisons are diagnostic, not proof of full game equivalence.',
]


def scene_simulate(raw, native_class=None, *, python_kernel=False, _validated=False):
    # Internal callers already validated and copied caller-owned input.
    s = raw if _validated else validate(raw)
    client_clock = s['version'] == '2.59.0.28' and s['dt_s'] == CLIENT_OBJECT_DT_S
    fixed_steps = client_clock or s.get('step_policy', 'version_default') == 'fixed'
    p, identity = load_profile(s['missile'], s['version'])
    launch = s['launch']
    velocity = np.array(launch['velocity_m_s']) + rotation(launch['quaternion_xyzw']) @ launch['ejection_velocity_body_m_s']
    if native_class is None:
        native_class = missile_class(s['version'])
    native = native_class(p, launch['position_m'], velocity,
                                                  launch['quaternion_xyzw'], launch['angular_rate_rad_s'])
    if native.version != s['version']:
        raise ValueError('Native ELF version and scenario resource version differ')
    if 'motor_start_times_s' in launch:
        native.setup_engine(launch['motor_start_times_s'])
    fast_engine = None
    if not python_kernel:
        raise ValueError('This single-file calculator supports only the Python kernel')
    if python_kernel:
        seeker_class = Seeker
        native.fastmode = s['fastmode']
        if s['fastmode']:
            initial_range = math.dist(launch['position_m'], s['target']['position_m'])
            if initial_range < 5000:
                native.fast_fallback_reason = 'Initial range below 5 km: retain standard arithmetic through close-pass sensitivity'
            else:
                native.fast_fallback_reason = None
                seeker_class = FastSeeker
                native.body = FastBody(p)
                native.controller.fast = True
    native.lastacc = np.array(launch['feedback_acceleration_m_s2'])
    native.feedback_acceleration = native.lastacc.copy()
    age0 = launch['age_s']
    target = Target(s['target'])
    output_target = Target(s['target'])  # Sampling must never mutate simulation truth.
    target.advance(0)
    locked = s['observation']['mode'] == 'locked'
    seeker = seeker_class(native, s['ground_height_m']) if locked else None
    transport = ObservationTransport(s['observation'], target.position, target.velocity) if locked else None
    observation, observation_age = None, 0.
    if locked:
        observed_p, observed_v, observation_age = transport.update(0, target.position, target.velocity)
        observation = seeker.update(0, s['dt_s'], observed_p, observed_v)
    lifetime = float(p['timeLife'])
    if age0 >= lifetime:
        raise ValueError('Initial missile age already reaches resource lifetime')
    remaining_life = lifetime-age0
    horizon = min(s['duration_s'], remaining_life)
    fuse = p.get('proximityFuse', {})
    fuse_radius = fuse.get('radius', 0) if p.get('hasProximityFuse', False) and s.get('proximity_fuse', True) else 0
    fuse_arm = max(0., fuse.get('timeOut', 0)-age0)
    boundaries = sorted(set([horizon, fuse_arm, *[x-age0 for x in native.engine_boundaries],
                             *[x['time_s'] for x in target.segments],
                             p['guidance']['guidanceAutopilot'].get('timeOut', 0)-age0]))
    t = 0.
    state = native.state()
    minimum = float(norm64(target.position - state[:3]))
    closest_time = 0.
    distance_flown = 0.
    samples = []
    peak_speed = float(norm64(state[7:10]))
    peak_request = peak_specific = peak_trajectory = 0.
    next_sample = 0.
    terminal = None
    controls = np.zeros(2)
    mass, thrust, tvc, _, _ = native.engine(age0)
    previous_acc = native.lastacc.copy()
    initial_event = first_event(0, 1e-9, state[:3], state[:3], target.position, target.position,
                                s['target']['collision_radius_m'], fuse_radius, fuse_arm, s['ground_height_m'])

    # 输出列 `aoa_eff_deg` 的力臂：与 `Body.forces()` / `FastBody.forces()` 用的是**同一个值**。
    output_lever = float(native.body.lever)

    def sample(at, st, tp, tv, *, output_mass=None, output_distance=None, interpolated=False):
        if at == target.time:
            target_acceleration = target.acceleration
        else:
            output_target.advance(at)
            target_acceleration = output_target.acceleration
        v = st[7:10]
        speed = float(norm64(v))
        relative = tp - st[:3]
        distance = float(norm64(relative))
        closing = -float(relative @ (tv-v)) / distance if distance > 1e-9 else None
        normal = previous_acc - v * float(previous_acc @ v) / speed**2 if speed > 1e-9 else None
        # 2026-10-02 新增输出列（契约见 docs/SOLVER-API.md §3.1）：mach / aoa_deg / aoa_eff_deg。
        # 与本文件 C 路 `record()` 的同一段逐字对应（两边都是 double、同一组 helper）。
        # ⚠ 攻角是 acos 出来的**非负角** [0°, 180°]；要带符号必须另立列名，这里不擅自加符号。
        # 马赫数的分子与 `air_speed_m_s` 用**同一个** `norm64(v - wind)` 表达式，所以
        # `mach * sound == air_speed_m_s` 恒成立（判据见 tests/test_solver_api.py）。
        air = v - s['wind_m_s']
        R = rotation(st[3:7])
        nose = unit(R[:, 0], (1.0, 0.0, 0.0))
        air_hat = unit(air, nose)
        flow = unit(air + R @ np.array([0.0, output_lever * st[12],
                                        -output_lever * st[11]]), air_hat)
        sound = _atmosphere(st[1])[1]
        mach = float(norm64(air)) / sound
        aoa_deg = math.degrees(math.acos(max(-1.0, min(1.0, float(nose @ air_hat)))))
        aoa_eff_deg = math.degrees(math.acos(max(-1.0, min(1.0, float(nose @ flow)))))
        return {'time_s': at, 'missile_age_s': age0+at, 'missile_x_m': float(st[0]), 'missile_y_m': float(st[1]), 'missile_z_m': float(st[2]),
                'missile_vx_m_s': float(st[7]), 'missile_vy_m_s': float(st[8]), 'missile_vz_m_s': float(st[9]),
                'qx': float(st[3]), 'qy': float(st[4]), 'qz': float(st[5]), 'qw': float(st[6]),
                'omega_x_rad_s': float(st[10]), 'omega_y_rad_s': float(st[11]), 'omega_z_rad_s': float(st[12]),
                'target_x_m': float(tp[0]), 'target_y_m': float(tp[1]), 'target_z_m': float(tp[2]),
                'target_vx_m_s': float(tv[0]), 'target_vy_m_s': float(tv[1]), 'target_vz_m_s': float(tv[2]),
                'range_m': distance, 'speed_m_s': speed,
                'mass_kg': mass if output_mass is None else output_mass, 'thrust_N': thrust, 'tvc_factor': tvc,
                'speed_kmh': speed*3.6, 'target_speed_m_s': float(norm64(tv)),
                'air_speed_m_s': float(norm64(v-s['wind_m_s'])),
                'closing_speed_m_s': closing,
                'linear_time_to_go_s': distance/closing if closing is not None and closing > 1e-9 else None,
                'specific_force_g': float(norm64(previous_acc+[0, 9.81, 0])/9.81) if at > 0 else None,
                'trajectory_normal_g': float(norm64(normal)/9.81) if at > 0 and normal is not None else None,
                'acceleration_valid': at > 0,
                'distance_flown_m': distance_flown if output_distance is None else output_distance,
                'state_interpolated': interpolated,
                'target_accel_x_m_s2': float(target_acceleration[0]), 'target_accel_y_m_s2': float(target_acceleration[1]), 'target_accel_z_m_s2': float(target_acceleration[2]),
                'control_horizontal': float(controls[0]), 'control_vertical': float(controls[1]),
                'requested_g': float(norm64(native.request) / 9.81),
                'guidance_velocity_source': native.guidance_velocity_source,
                'guidance_vx_m_s': float(native.guidance_velocity[0]), 'guidance_vy_m_s': float(native.guidance_velocity[1]), 'guidance_vz_m_s': float(native.guidance_velocity[2]),
                'feedback_accel_x_m_s2': float(native.feedback_acceleration[0]), 'feedback_accel_y_m_s2': float(native.feedback_acceleration[1]), 'feedback_accel_z_m_s2': float(native.feedback_acceleration[2]),
                'observation_mode': s['observation']['mode'], 'locked': True,
                'observation_time_s': seeker.last_time if seeker else at,
                'source_observation_age_s': observation_age + max(0., at-seeker.last_time) if seeker else 0.,
                'observed_range_m': observation['range_m'] if observation else distance,
                'observed_range_channel_valid': observation.get('range_valid', False) if observation else False,
                'observed_doppler_channel_valid': observation.get('doppler_valid', False) if observation else False,
                'observed_los_x': float(observation['direction'][0]) if observation else None, 'observed_los_y': float(observation['direction'][1]) if observation else None, 'observed_los_z': float(observation['direction'][2]) if observation else None,
                'observed_omega_x_rad_s': float(observation['omega'][0]) if observation else None, 'observed_omega_y_rad_s': float(observation['omega'][1]) if observation else None, 'observed_omega_z_rad_s': float(observation['omega'][2]) if observation else None,
                'accel_x_m_s2': float(previous_acc[0]), 'accel_y_m_s2': float(previous_acc[1]), 'accel_z_m_s2': float(previous_acc[2]),
                'mach': mach, 'aoa_deg': aoa_deg, 'aoa_eff_deg': aoa_eff_deg}

    samples.append(sample(0, state, target.position, target.velocity))
    step_output = s['sample_period_s'] is None
    next_sample = math.inf if step_output else s['sample_period_s']
    sample_index = 1
    steps = 0
    if initial_event:
        terminal = initial_event[1]
    while t < horizon - 1e-10 and terminal is None:
        if locked:
            observed_p, observed_v, observation_age = transport.update(t, target.position, target.velocity)
            if t > 0:
                observation = seeker.update(t, s['dt_s'], observed_p, observed_v)
                if s['fastmode']:
                    if seeker.last_time == t:
                        seeker.source_age_at_filter = observation_age
                    else:
                        observation_age = seeker.source_age_at_filter
        # Keep the physics grid anchored to launch even when a model boundary
        # inserts a short step. Output timestamps never split native updates.
        next_tick = (math.floor((t+1e-10)/s['dt_s'])+1)*s['dt_s']
        dt = min(next_tick-t, horizon-t)
        if transport and not fixed_steps:
            dt = min(dt, transport.boundary()-t)
        # The recovered object loop advances a full tick across motor/control
        # thresholds. Splitting near float32 grid points can invent tiny PID
        # updates. Keep boundary splitting only for explicit offline fine steps.
        for boundary in (() if fixed_steps else boundaries):
            if t + 1e-9 < boundary < t + dt:
                dt = boundary - t
                break
        old = state.copy()
        old_mass, old_distance, old_time = mass, distance_flown, t
        tp0, tv0 = target.position.copy(), target.velocity.copy()
        controls = native.guide(age0+t, dt, tp0, tv0, observation=observation)
        mass, thrust, tvc, body_force, torque = native.engine(age0+t+dt, controls)
        body_rotation = native.body_rotation() if python_kernel else rotation(old[3:7])
        state, controls = native.step(age0+t+dt, dt, native.request, mass, body_rotation @ body_force,
                                      torque, s['wind_m_s'], controls=controls, gravity=True)
        target.advance(t + dt)
        event = first_event(t, dt, old[:3], state[:3], tp0, target.position,
                            s['target']['collision_radius_m'], fuse_radius, fuse_arm, s['ground_height_m'])
        fraction = event[0] if event else 1.
        r0 = tp0 - old[:3]
        r1 = target.position - state[:3]
        cp = min(fraction, closest_fraction(r0, r1))
        cpa = float(norm64(r0 + cp * (r1 - r0)))
        if cpa < minimum:
            minimum, closest_time = cpa, t + cp * dt
        distance_flown += float(norm64(state[:3] - old[:3])) * fraction
        previous_acc = native.lastacc.copy()
        if event:
            terminal = event[1]
            state = old + fraction * (state - old)
            state[3:7] /= norm64(state[3:7])
            target.position = tp0 + fraction * (target.position - tp0)
            target.velocity = tv0 + fraction * (target.velocity - tv0)
            mass, thrust, tvc, _, _ = native.engine(age0+t+fraction*dt, controls)
        speed = float(norm64(state[7:10]))
        peak_speed = max(peak_speed, speed)
        peak_request = max(peak_request, float(norm64(native.request) / 9.81))
        peak_specific = max(peak_specific, float(norm64(previous_acc + [0, 9.81, 0]) / 9.81))
        if speed > 1e-6:
            vh = state[7:10] / speed
            peak_trajectory = max(peak_trajectory, float(norm64(previous_acc - (previous_acc @ vh) * vh) / 9.81))
        steps += 1
        t += dt * fraction
        if step_output:
            samples.append(sample(t, state, target.position, target.velocity,
                                  interpolated=bool(event and fraction < 1.)))
        while next_sample <= t+1e-10:
            if abs(next_sample-t) <= 1e-10:
                samples.append(sample(next_sample, state, target.position, target.velocity,
                                      interpolated=bool(event and fraction < 1.)))
            else:
                ratio = (next_sample-old_time)/(t-old_time)
                sampled = old + ratio*(state-old)
                # Normalized quaternion interpolation, preserving the shortest arc.
                end_q = state[3:7] if old[3:7] @ state[3:7] >= 0 else -state[3:7]
                sampled[3:7] = old[3:7] + ratio*(end_q-old[3:7])
                sampled[3:7] /= norm64(sampled[3:7])
                output_target.advance(next_sample)
                samples.append(sample(next_sample, sampled, output_target.position, output_target.velocity,
                                      output_mass=old_mass+ratio*(mass-old_mass),
                                      output_distance=old_distance+ratio*(distance_flown-old_distance),
                                      interpolated=True))
            sample_index += 1
            next_sample = sample_index*s['sample_period_s']
        if (event or t >= horizon-1e-10) and abs(samples[-1]['time_s']-t) > 1e-10:
            samples.append(sample(t, state, target.position, target.velocity,
                                  interpolated=bool(event and fraction < 1.)))
    terminal = terminal or ('lifetime' if remaining_life <= s['duration_s'] else 'time_limit')
    mode_limit = ('Native angle and available range/Doppler filters and radar multipath; absent channels use supplied scene geometry marked invalid as sensor measurements. Observation transport/bias is a scenario adapter. Default external latency is unspecified/zero, not a recovered game constant.' if locked else
                  'Ideal instantaneous target geometry; seeker filters, multipath and transport bypassed for comparison only.')
    summary = {'identity': identity, 'model': 'native-'+s['version']+'-'+s['observation']['mode']+('-fastmode' if s['fastmode'] else ''), 'terminal_event': terminal,
               'fastmode': {'enabled': s['fastmode'], 'physics_step_unchanged': True,
                            'compiled_budget_sha256': getattr(native, 'accelerator_identity', None),
                            'engine_calls': fast_engine.calls if fast_engine else None,
                            'engine_native_fallback_calls': fast_engine.fallback_calls if fast_engine else None,
                            'engine_fallback_reason': fast_engine.reason if fast_engine else None,
                            'predicted_observations': seeker.predictions if locked and s['fastmode'] else 0,
                            'seeker_fallback_reason': seeker.fallback_reason if locked and s['fastmode'] else None,
                            'approximation': ('Emulated x87 libm; float64 fixed-width observation vectors; native-calibrated piecewise linear mass/constant thrust; distant seeker updates every four ticks with LOS/range prediction, per-tick near target/startup/ground/high LOS rate. No universal accuracy bound.' if s['fastmode'] else None)},
               'seeker_assumptions': {'permanent_lock': True, 'unlimited_acquisition': True,
                                      'unlimited_tracking_rate': True, 'unlimited_off_boresight': True},
               'observation': {**s['observation'], 'filter_updates': seeker.updates if seeker else 0,
                               'multipath_updates': seeker.multipath_updates if seeker else 0,
                               'packets_captured': transport.count if transport else 0,
                               'packets_received': transport.received if transport else 0,
                               'initial_track': 'pre-acquired at time zero; zero initial angular-rate estimate'},
               'time_s': t, 'initial_age_s': age0, 'steps': steps, 'dt_s': s['dt_s'], 'minimum_separation_until_event_m': minimum,
               'closest_time_s': closest_time, 'distance_flown_m': distance_flown,
               'final_speed_m_s': float(norm64(state[7:10])), 'peak_speed_m_s': peak_speed,
               'peak_requested_g_before_native_limit': peak_request, 'peak_specific_force_g': peak_specific,
               'peak_trajectory_normal_g': peak_trajectory, 'kill_assessed': False,
               'event_interpolation': 'linear within final physics step',
               'sample_period_s': s['sample_period_s'],
               'output_mode': 'steps' if step_output else 'periodic',
               'timing': {'matches_recovered_client_default': client_clock,
                          'step_policy': s.get('step_policy', 'version_default'), 'fixed_steps': fixed_steps,
                          'client_default_dt_s': CLIENT_OBJECT_DT_S if s['version'] == '2.59.0.28' else None,
                          'evidence': '2.59.0.28 static rocket object loop 0x201fa00 and constructor 0x203df40; runtime overrides/server cadence unverified',
                          'physics_grid': ('launch-anchored fixed ticks; only horizon clips the last integration step; control/transport polled on ticks'
                                           if fixed_steps else 'launch-anchored explicit offline steps with engine, activation, observation, target and termination boundary splits'),
                          'output_affects_physics': False},
               'output_interpolation': ('Initial state and every actual physics endpoint; only a within-step terminal event is interpolated.' if step_output else 'Off-grid missile position/velocity/angular rate/mass/path are linear, quaternion normalized; target is analytic. Control, force and observation channels are held from the containing integration interval. No extra native calls for output.'),
               'motor_start_times_s': getattr(native, 'motor_start_times', None),
               'motor_ignition_policy': 'Explicit launch motor ages plus resource delays, or sequential offline default; independent-motor game ignition triggers are not recovered.',
               'limitations': [mode_limit, *[x.replace('2.59.0.28', s['version']) for x in BOUNDARIES]],
               'fuse_radius_m': fuse_radius, 'fuse_arm_time_s': fuse_arm}
    if python_kernel:
        summary['model'] = 'python-game-6dof-'+s['version']+('-fastmode' if s['fastmode'] else '')
        summary['elf_executed'] = False
        summary['identity'] = {k:v for k,v in identity.items() if k != 'elf_sha256'}
        summary['formula_basis_elf_sha256'] = identity.get('elf_sha256')
        summary['fastmode']['approximation'] = ('Scalar rigid-body force evaluation; relaxed intermediate PID/filter rounding and removed redundant observation frame round-trip. Every filter, guidance, PID, body and output tick is retained. No universal trajectory error bound.' if s['fastmode'] else None)
        summary['fastmode']['fallback_reason'] = getattr(native,'fast_fallback_reason',None)
        summary['fastmode']['math_acceleration_active'] = bool(s['fastmode'] and not getattr(native,'fast_fallback_reason',None))
        summary['limitations'] = [
            'Direct Python port of recovered PN/loft, PID, finite-stage engine/TVC, three-dimensional air force/torque, rigid-body integration and tracking filters; floating-point results are not guaranteed bit-identical.',
            'Permanent-lock and unlimited seeker-rate/domain assumptions remain explicit; transport timing is supplied by the scenario.',
            'End-speed postprocess, engine inputs to limitAoa PID and pressure-to-thrust curve are connected; the older native adapter omits these branches.',
            'Unsupported orientation/propulsion outer controllers and ambiguous duplicate endSpeed resources are rejected; no silent point-mass fallback.',
            *BOUNDARIES[4:]]
        if locked:
            summary['limitations'].append('Python ports of angle/range/Doppler filters and multipath; no complete echo, carrier-radar or datalink reconstruction.')
    return s, summary, samples

# ---- calculator.py ----
"""Screenshot-unit inputs -> scenario; output every actual physics step."""
import math

# Required values deliberately have no defaults in the parser: typos must not
# silently replace user conditions. The example file supplies the screenshot.
FIELDS = {
    'launch_speed_kmh': '起始速度（公里/小时）',
    'launch_altitude_m': '发射高度（米）',
    'launch_pitch_deg': '发射角度（度）',
    'launch_yaw_deg': '发射偏航角（度）',
    'target_speed_kmh': '目标速度（公里/小时；允许负值）',
    'target_altitude_m': '目标高度（米）',
    'target_distance_m': '初始目标距离（三维斜距，米）',
    'target_bearing_deg': '目标方位角（度）',
    'target_heading_deg': '目标航向（度）',
    'target_turn_g': '目标恒定 G 水平转弯（正值向 +z 转）',
    'target_pitch_deg': '目标垂直航向（度）',
}
CONVENTIONS = {
    'coordinates': 'x/z horizontal, y up; zero heading is +x, positive heading towards +z; angles are absolute in this fixed frame.',
    'distance': '3D slant distance; horizontal range = sqrt(distance^2 - altitude_difference^2).',
    'launch': 'Velocity and missile body axis initially aligned to launch pitch/yaw; no implicit rack ejection speed.',
    'negative_target_speed': 'Negates all three components of the heading/pitch velocity; does not silently take absolute value.',
    'turn': 'Constant horizontal centripetal acceleration = abs(G)*9.81, constant total speed and pitch; sign selects heading rotation. Not aircraft aerodynamic load factor.',
    'sampling': 'Initial state and every actual physics step through termination. The final event can lie within a step; its interpolated state is also saved in event.json. No padding after termination.',
    'model': 'Exact-build native guidance/body routines in a synthetic offline world; permanent unlimited seeker lock, retained native observation filtering. Not game flight or damage truth.',
}


def to_scenario(raw):
    if not isinstance(raw, dict):
        raise ValueError('Calculator input must be an object')
    optional = {'missile', 'version', 'duration_s', 'dt_s', 'motor_start_times_s', 'fastmode'}
    unknown, missing = set(raw)-set(FIELDS)-optional, set(FIELDS)-set(raw)
    if unknown or missing:
        raise ValueError(f'Unknown input fields: {sorted(unknown)}; missing: {sorted(missing)}')
    for key in ('dt_s', 'duration_s'):
        if key in raw and (isinstance(raw[key], bool) or not isinstance(raw[key], (int, float)) or not math.isfinite(raw[key])):
            raise ValueError(f'{key} must be a finite number')
    values = {}
    for key in FIELDS:
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number')
        values[key] = float(value)
    v = values
    if not 0 <= v['launch_speed_kmh'] <= 20000 or abs(v['target_speed_kmh']) > 20000:
        raise ValueError('Launch speed must be 0..20000 km/h; signed target speed within +/-20000')
    for key in ('launch_pitch_deg', 'target_pitch_deg'):
        if abs(v[key]) > 90:
            raise ValueError(f'{key} must be within [-90, 90]')
    for key in ('launch_yaw_deg', 'target_bearing_deg', 'target_heading_deg'):
        if abs(v[key]) > 360:
            raise ValueError(f'{key} must be within [-360, 360]')
    if any(not 0 < v[k] <= 100000 for k in ('launch_altitude_m', 'target_altitude_m')):
        raise ValueError('Altitudes must be above flat ground and <= 100000 m')
    if not 0 < v['target_distance_m'] <= 1000000 or abs(v['target_turn_g']) > 100:
        raise ValueError('Distance must be 0..1000000 m and |turn G| <= 100')
    dh = v['target_altitude_m']-v['launch_altitude_m']
    if v['target_distance_m'] < abs(dh):
        raise ValueError('Slant range cannot be smaller than the altitude difference')
    horizontal = math.sqrt(max(0., v['target_distance_m']**2-dh**2))
    pitch, yaw, bearing, heading, target_pitch = [math.radians(v[k]) for k in
        ('launch_pitch_deg', 'launch_yaw_deg', 'target_bearing_deg', 'target_heading_deg', 'target_pitch_deg')]
    def velocity(speed, pitch, heading):
        return [speed*math.cos(pitch)*math.cos(heading), speed*math.sin(pitch), speed*math.cos(pitch)*math.sin(heading)]
    # q_y(-yaw) * q_z(pitch), Hamilton xyzw, body +x is forward.
    sy, cy, sp, cp = math.sin(yaw/2), math.cos(yaw/2), math.sin(pitch/2), math.cos(pitch/2)
    target_velocity = velocity(v['target_speed_kmh']/3.6, target_pitch, heading)
    horizontal_speed = math.hypot(target_velocity[0], target_velocity[2])
    if v['target_turn_g'] and horizontal_speed < 1e-8:
        raise ValueError('Nonzero horizontal turn G requires nonzero horizontal target speed')
    version, missile = raw.get('version', '2.59.0.28'), raw.get('missile', 'cn_pl12')
    params, _ = load_profile(missile, version)
    scenario = {'schema_version': 1, 'version': version, 'missile': missile,
                'fastmode': raw.get('fastmode', False),
                'duration_s': raw.get('duration_s', params['timeLife']), 'dt_s': raw.get('dt_s', default_dt(version)),
                'sample_period_s': None,
                'launch': {'position_m': [0, v['launch_altitude_m'], 0],
                           'velocity_m_s': velocity(v['launch_speed_kmh']/3.6, pitch, yaw),
                           'quaternion_xyzw': [-sy*sp, -sy*cp, cy*sp, cy*cp]},
                'target': {'position_m': [horizontal*math.cos(bearing), v['target_altitude_m'], horizontal*math.sin(bearing)],
                           'velocity_m_s': target_velocity,
                           'turn_rate_rad_s': v['target_turn_g']*9.81/horizontal_speed if horizontal_speed >= 1e-8 else 0}}
    if 'motor_start_times_s' in raw:
        scenario['launch']['motor_start_times_s'] = raw['motor_start_times_s']
    return validate(scenario)


def save_calculation(folder, raw, scenario, summary, rows, *, compact=False):
    summary = {**summary, 'input_conventions': CONVENTIONS, 'output_rows': len(rows),
               'output_columns': len(rows[0]), 'output_period_s': scenario['sample_period_s']}
    save(folder, scenario, summary, rows)
    write_json(folder/'trajectory.json', rows, compact=compact)
    write_json(folder/'event.json', {'event': summary['terminal_event'], 'state': rows[-1], 'kill_assessed': False})
    write_json(folder/'input.json', raw)
    return summary, rows

# ---- python_api.py ----
"""Public API for the standalone, NumPy-only calculator."""
import argparse
import json
from pathlib import Path
import sys



class Calculator:
    """Reusable stateless handle; every call gets independent controller state."""
    engine = 'python'

    def calculate(self, inputs, *, input_format='auto', fastmode=None):
        if not isinstance(inputs, dict):
            raise ValueError('Input must be a dictionary / JSON object')
        if input_format not in ('auto', 'scenario', 'calculator'):
            raise ValueError('input_format must be auto, calculator or scenario')
        if fastmode is not None and not isinstance(fastmode, bool):
            raise ValueError('fastmode must be boolean')
        kind = input_format
        if kind == 'auto':
            kind = 'scenario' if any(k in inputs for k in ('launch', 'target', 'schema_version')) else 'calculator'
        raw = dict(inputs)
        if fastmode is not None:
            raw['fastmode'] = fastmode
        if kind == 'scenario':
            raw.setdefault('schema_version', 1)
            scenario = validate(raw)
        else:
            scenario = to_scenario(raw)
        normalized, summary, rows = python_simulate(scenario, _validated=True)
        summary = {**summary, 'engine': 'python', 'output_rows': len(rows), 'output_columns': len(rows[0])}
        return {'schema_version': 1, 'engine': 'python', 'input_format': kind,
                'scenario': normalized, 'summary': summary, 'trajectory': rows,
                'event': {'event': summary['terminal_event'], 'state': rows[-1], 'kill_assessed': False}}


def calculate(inputs, *, input_format='auto', fastmode=None):
    return Calculator().calculate(inputs, input_format=input_format, fastmode=fastmode)


def list_missiles(version='2.59.0.28'):
    if version not in PROFILES_BY_VERSION:
        raise ValueError('Unsupported resource version')
    result = []
    for name in PROFILES_BY_VERSION[version]:
        reason = profile_support(name, version)
        result.append({'missile': name, 'version': version, 'seeker': PRESETS.get(name, {}).get('seeker'),
                       'supported': reason is None, 'unsupported_reason': reason})
    return result


def save_result(result, directory):
    folder = Path(directory).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    save(folder, result['scenario'], result['summary'], result['trajectory'])
    write_json(folder/'trajectory.json', result['trajectory'], compact=True)
    write_json(folder/'event.json', result['event'])
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', help='JSON file or - for stdin')
    parser.add_argument('--format', choices=('auto', 'scenario', 'calculator'), default='auto')
    parser.add_argument('--fastmode', action='store_const', const=True, default=None,
                        help='Opt-in approximate arithmetic (.28 only); standard precision is the default')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--save-dir', type=Path)
    parser.add_argument('--list-missiles', action='store_true')
    parser.add_argument('--version', default='2.59.0.28')
    args = parser.parse_args(argv)
    try:
        if args.list_missiles:
            result = list_missiles(args.version)
        else:
            if not args.input:
                parser.error('an input file or --list-missiles is required')
            raw = json.loads(sys.stdin.read() if args.input == '-' else Path(args.input).read_text(encoding='utf-8-sig'))
            result = calculate(raw, input_format=args.format, fastmode=args.fastmode)
            if args.save_dir:
                save_result(result, args.save_dir)
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)+'\n'
        if args.output:
            args.output.write_text(serialized, encoding='utf-8')
        else:
            sys.stdout.write(serialized)
    except (ValueError, KeyError, OSError, TypeError, RuntimeError) as error:
        print(f'Calculation failed: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
