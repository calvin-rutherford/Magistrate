import { clampAudioPeak } from './VoiceVisuals';

export type Point3 = readonly [number, number, number];
export type ProjectedFace = { points: string; depth: number; light: number; index: number };

export const TETRAHEDRON_VERTICES: readonly Point3[] = [
  [1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1],
];
export const TETRAHEDRON_FACES = [[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]] as const;

function rotate([x, y, z]: Point3, pitch: number, yaw: number, roll: number): Point3 {
  const cy = Math.cos(yaw); const sy = Math.sin(yaw);
  const cx = Math.cos(pitch); const sx = Math.sin(pitch);
  const cz = Math.cos(roll); const sz = Math.sin(roll);
  const x1 = x * cy + z * sy; const z1 = -x * sy + z * cy;
  const y2 = y * cx - z1 * sx; const z2 = y * sx + z1 * cx;
  return [x1 * cz - y2 * sz, x1 * sz + y2 * cz, z2];
}

function cross(a: Point3, b: Point3): Point3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

/** Rotate true 3D geometry, perspective-project it, and depth-sort its faces. */
export function projectTetrahedron(phase: number, amplitude = 0, size = 240): ProjectedFace[] {
  const safePhase = Number.isFinite(phase) ? phase : 0;
  const energy = clampAudioPeak(amplitude);
  const scale = size * (0.205 + energy * 0.025);
  const camera = 5.2;
  const rotated = TETRAHEDRON_VERTICES.map(vertex => rotate(
    vertex, -0.34 + Math.sin(safePhase * 0.41) * 0.08, safePhase, 0.12 + Math.cos(safePhase * 0.27) * 0.06,
  ));
  const center = size / 2;
  return TETRAHEDRON_FACES.map((face, index) => {
    const vertices = face.map(vertex => rotated[vertex]);
    const points = vertices.map(([x, y, z]) => {
      const perspective = camera / (camera - z);
      return `${(center + x * scale * perspective).toFixed(2)},${(center + y * scale * perspective).toFixed(2)}`;
    }).join(' ');
    const a: Point3 = [vertices[1][0] - vertices[0][0], vertices[1][1] - vertices[0][1], vertices[1][2] - vertices[0][2]];
    const b: Point3 = [vertices[2][0] - vertices[0][0], vertices[2][1] - vertices[0][1], vertices[2][2] - vertices[0][2]];
    const normal = cross(a, b); const normalLength = Math.hypot(...normal) || 1;
    const light = Math.max(0.18, Math.min(1, 0.46 + (normal[0] * -0.25 + normal[1] * -0.42 + normal[2] * 0.87) / normalLength * 0.54));
    return { points, depth: vertices.reduce((sum, vertex) => sum + vertex[2], 0) / 3, light, index };
  }).sort((left, right) => left.depth - right.depth);
}
