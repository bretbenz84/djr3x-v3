"""Native Qt Quick 3D avatar (V2 gui/rex_avatar_3d.py, adapted).

The rigged droid follows each joint's output pulse as the board ramps it: the servo
owner's packets replayed at their speed and acceleration (controller output, not joint
feedback) and the LED owner's commands; see rig.py and lights.py. Without a
servo controller (dev Mac) the dashboard's simulated body (sim.py) moves it.
"""
from __future__ import annotations
import json
import math
from functools import lru_cache
from PySide6.QtCore import QByteArray, QObject, Property, QSize, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QColor, QMatrix3x3, QQuaternion, QVector3D
from PySide6.QtQuick3D import QQuick3DGeometry, QQuick3DTextureData
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .lights import EyeBlink, MouthAnimation, chest_render_state, lamp_colors
from .rig import ASSET_DIR, AWAKE_PULSES, BOOT_PULSES, pose_matrices, rig_values, smooth, spring_points

SMOOTHING = .6   # Fraction of the remaining distance per frame (V2 eased commanded targets at .25).


@lru_cache(maxsize=1)
def _geometry_data():
    import numpy as np
    with np.load(ASSET_DIR / 'geometry.npz', allow_pickle=False) as archive:
        return {key: archive[key] for key in archive.files}

class DisplayGeometry(QQuick3DGeometry):
    def __init__(self, vertices, uv=None):
        super().__init__()
        self._uv = uv
        self.setStride(32)
        self.setPrimitiveType(self.PrimitiveType.Triangles)
        self.addAttribute(self.Attribute.Semantic.PositionSemantic, 0, self.Attribute.ComponentType.F32Type)
        self.addAttribute(self.Attribute.Semantic.NormalSemantic, 12, self.Attribute.ComponentType.F32Type)
        self.addAttribute(self.Attribute.Semantic.TexCoordSemantic, 24, self.Attribute.ComponentType.F32Type)
        self.replace(vertices)

    def replace(self, vertices):
        import numpy as np
        uv = self._uv if self._uv is not None else np.column_stack(((vertices[:,0]*.786 + vertices[:,1]*.618 + .15)/.30, np.full(len(vertices), .5)))
        a = np.ascontiguousarray(np.concatenate((vertices, uv), axis=1), dtype='<f4')
        self.setVertexData(QByteArray(a.tobytes()))
        self.setBounds(QVector3D(*a[:, :3].min(axis=0)), QVector3D(*a[:, :3].max(axis=0)))
        self.update()


class MouthDiffuser(QQuick3DTextureData):
    """Small, shared emission map: resin scatters each LED into its neighbors.

    Sample the exported aperture positions, preserving the physical row/column
    mapping and curved grille. No added lights or per-frame geometry uploads.
    """
    def __init__(self, patches):
        import numpy as np
        super().__init__()
        self.vertices = np.concatenate(patches)
        def project(v):
            import numpy as np
            return np.column_stack((v[:, 0] * .786 + v[:, 1] * .618, v[:, 2]))
        projected = project(self.vertices)
        lo, hi = projected.min(0), projected.max(0)
        self.uv = (projected - lo) / (hi - lo)
        centers = np.array([project(v).mean(0) for v in patches])
        axis = (np.arange(64) + .5) / 64
        u, v = np.meshgrid(axis, axis)
        points = lo + np.stack((u, v), axis=-1).reshape(-1, 2) * (hi - lo)
        # About one LED pitch: distinct bars remain, but their edges blend.
        distance = ((points[:, None, :] - centers[None, :, :]) / .0045) ** 2
        weights = np.exp(-.5 * distance.sum(axis=2))
        self._weights = (weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)).astype(np.float32)
        self.setSize(QSize(64, 64))
        self.setFormat(QQuick3DTextureData.Format.RGBA8)
        self.setHasTransparency(False)
        self.render(np.zeros((80, 3)))

    def render(self, pixels):
        import numpy as np
        rgb = np.einsum('ij,jk->ik', self._weights, pixels, optimize=False)
        rgba = np.full((4096, 4), 255, dtype=np.uint8)
        # Texture RGB is sRGB; preserve linear LED energy through decoding.
        rgb = np.clip(rgb, 0, 1)
        srgb = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - .055)
        rgba[:, :3] = np.rint(srgb * 255).astype(np.uint8)
        self.setTextureData(QByteArray(rgba.tobytes()))


@lru_cache(maxsize=1)
def _studio_probe_data():
    """Linear HDR illumination: softboxes over a dark floor, not uniform fill."""
    import numpy as np
    longitude, latitude = np.meshgrid(
        (np.arange(512) + .5) * (2 * np.pi / 512) - np.pi,
        np.pi / 2 - (np.arange(256) + .5) * (np.pi / 256),
    )
    direction = np.stack((np.sin(longitude) * np.cos(latitude),
                          np.sin(latitude), np.cos(longitude) * np.cos(latitude)), axis=-1)
    rgb = np.broadcast_to(np.array([.035, .045, .06]), (256,512,3)).copy()
    rgb *= (.25 + .75 * np.clip(direction[:,:,1:2] + .2, 0, 1))
    # Broad warm key, weaker cool fill, narrow rear reflection. Their finite
    # extent produces gradients across metal, with dark regions between them.
    for center, width, height, color in (
        ((-.65,.65,.5), .38,.65, (4.5,4.1,3.5)),
        ((.8,.25,.5), .45,.75, (.65,.85,1.1)),
        ((.3,.5,-.8), .16,.6, (2.,2.4,3.)),
    ):
        normal = np.array(center); normal /= np.linalg.norm(normal)
        right = np.cross([0,1,0],normal); right /= np.linalg.norm(right)
        up = np.cross(normal,right)
        forward = direction @ normal
        u = (direction @ right) / np.maximum(forward,.001)
        v = (direction @ up) / np.maximum(forward,.001)
        box = np.exp(-((u/width)**4 + (v/height)**4)) * (forward > 0)
        rgb += box[:,:,None] * color
    rgba = np.ones((256,512,4),dtype='<f4'); rgba[:,:,:3] = rgb
    return rgba.tobytes()


class StudioLightProbe(QQuick3DTextureData):
    def __init__(self):
        super().__init__()
        self.setSize(QSize(512,256))
        self.setFormat(QQuick3DTextureData.Format.RGBA32F)
        self.setHasTransparency(False)
        self.setTextureData(QByteArray(_studio_probe_data()))

def tube_vertices(points, radius, sides=8):
    """Round tube following an updated centreline, without stretching wire diameter."""
    import numpy as np
    p = np.asarray(points)
    tangent = np.gradient(p, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None]
    reference = np.tile([0., 0., 1.], (len(p), 1))
    reference[np.abs(tangent[:, 2]) > .95] = [1., 0., 0.]
    u = np.cross(tangent, reference)
    u /= np.linalg.norm(u, axis=1)[:, None]
    v = np.cross(tangent, u)
    a = np.arange(sides) * 2*math.pi/sides
    normals = u[:, None, :] * np.cos(a)[None, :, None] + v[:, None, :] * np.sin(a)[None, :, None]
    vertices = np.concatenate((p[:, None, :] + radius*normals, normals), axis=2).reshape(-1, 6)
    i = np.arange(len(p)-1)[:, None]*sides + np.arange(sides)[None, :]
    j = np.arange(len(p)-1)[:, None]*sides + (np.arange(sides)[None, :]+1)%sides
    indices = np.stack((i, j, i+sides, j, j+sides, i+sides), axis=-1).reshape(-1)
    return vertices[indices].astype('<f4')

class RigViewState(QObject):
    changed = Signal()
    def __init__(self):
        import numpy as np
        super().__init__()
        self._transforms = {}
        self._eye = QColor('black')
        self.mouth_animation = MouthAnimation()
        self.blink = EyeBlink()
        self._led = [QColor('black') for _ in range(9)]
        self._background = True
        self._light_probe = StudioLightProbe()
        self.geometries = []
        self._batches = []
        batches = json.loads((ASSET_DIR/'materials.json').read_text())
        mouth = sorted((b for b in batches if b['kind'] == 'mouth'), key=lambda b: b['lamp'])
        self._diffuser = MouthDiffuser([_geometry_data()[b['id']] for b in mouth])
        for batch in batches:
            if batch['kind'] == 'mouth':
                continue
            vertices = _geometry_data()[batch['id']]
            if batch['id'] == 'mesh_19':
                # This export batches three disconnected dark parts together.
                # Only the lower head plate (z .755-.790 m) is silver; preserve
                # the upper internal mechanism and side insert materials.
                triangles = vertices.reshape(-1, 3, 6)
                underside = np.all(triangles[:, :, 2] < .8, axis=1)
                plate = DisplayGeometry(triangles[underside].reshape(-1, 6))
                self.geometries.append(plate)
                silver = next(b for b in self._batches if b['id'] == 'mesh_15')
                self._batches.append(dict(silver, id='head_underside', geometry=plate))
                vertices = triangles[~underside].reshape(-1, 6)
            geometry = DisplayGeometry(vertices)
            self.geometries.append(geometry)
            # Blender stores linear RGB; QColor/QML base colors are sRGB.
            srgb = [12.92*c if c <= .0031308 else 1.055*c**(1/2.4)-.055 for c in batch['color']]
            color = QColor.fromRgbF(*srgb)
            self._batches.append(dict(batch, geometry=geometry, color=color))
        geometry = DisplayGeometry(self._diffuser.vertices, self._diffuser.uv)
        self.geometries.append(geometry)
        self._batches.append(dict(id='mouth_resin', group='head', geometry=geometry,
                                 color=QColor('#65716d'), metalness=0., roughness=.7,
                                 kind='mouth', lamp=-1))
        self.spring = DisplayGeometry(tube_vertices(spring_points(pose_matrices(rig_values(BOOT_PULSES))), .005))
        self.geometries.append(self.spring)
        self._batches.append(dict(id='spring', group='fixed', geometry=self.spring,
                                 color=QColor('#10181c'), metalness=.6,
                                 roughness=.35, kind='surface', lamp=-1))

    batches = Property('QVariantList', lambda self: self._batches, constant=True)
    transforms = Property('QVariantMap', lambda self: self._transforms, notify=changed)
    mouthTexture = Property(QObject, lambda self: self._diffuser, constant=True)
    lightProbe = Property(QObject, lambda self: self._light_probe, constant=True)
    eyeColor = Property(QColor, lambda self: self._eye, notify=changed)
    ledColors = Property('QVariantList', lambda self: self._led, notify=changed)
    background = Property(bool, lambda self: self._background, notify=changed)

    def pose(self, values):
        matrices = pose_matrices(values)
        self._transforms = {
            name: dict(position=QVector3D(*m[:3, 3]), rotation=QQuaternion.fromRotationMatrix(QMatrix3x3(m[:3, :3].reshape(-1).tolist())))
            for name, m in matrices.items()
        }
        self.spring.replace(tube_vertices(spring_points(matrices), .005))

class RexAvatar(QWidget):
    def __init__(self, feed, parent=None):
        super().__init__(parent)
        self._feed = feed
        self._snapshot = {}
        self._current = rig_values(BOOT_PULSES)
        self._rig = RigViewState()
        self._rig.pose(self._current)
        self._view = QQuickWidget(self)
        self._view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._view.setClearColor(QColor('#07111a'))
        self._view.rootContext().setContextProperty('rig', self._rig)
        self._view.setSource(QUrl.fromLocalFile(str(ASSET_DIR/'Avatar.qml')))
        if self._view.status() == QQuickWidget.Status.Error:
            raise RuntimeError('3D avatar failed to load: ' + '; '.join(e.toString() for e in self._view.errors()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self.setMinimumSize(280, 240)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick_3d)
        self._timer.start()

    def sizeHint(self):
        return QSize(430, 400)

    def set_snapshot(self, snapshot):
        self._snapshot = snapshot

    def _tick_3d(self):
        if not self.isVisible():
            return
        servos = self._snapshot.get('servos') or {}
        pulses = servos.get('positions') or {}
        if not servos.get('controller') and self._snapshot.get('status') == 'online':
            pulses = AWAKE_PULSES
        # Positions already follow the board's speed and acceleration ramps; the light easing
        # only hides the 20 Hz snapshot steps between 30 Hz frames.
        self._current = smooth(self._current, rig_values(pulses), SMOOTHING)
        self._rig.pose(self._current)
        lights = self._feed.lights.snapshot()
        head, now = lights['head'], self._feed.clock()
        brightness = self._rig.blink.brightness(head)
        self._rig._eye = QColor(*(int(c * brightness) for c in head['eye_color']))
        self._rig._led = [QColor(*rgb) for rgb in lamp_colors(chest_render_state(lights['chest'], now), now)]
        self._rig._diffuser.render(self._rig.mouth_animation.render(head, now))
        self._rig.changed.emit()
