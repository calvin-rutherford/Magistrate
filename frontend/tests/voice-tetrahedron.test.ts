/// <reference types="node" />
import assert from 'node:assert/strict';
import test from 'node:test';
import { projectTetrahedron, TETRAHEDRON_FACES, TETRAHEDRON_VERTICES } from '../src/services/VoiceTetrahedronGeometry.ts';

test('voice object is a true four-vertex, four-face tetrahedron', () => {
  assert.equal(TETRAHEDRON_VERTICES.length, 4);
  assert.equal(TETRAHEDRON_FACES.length, 4);
  const distances = new Set<string>();
  for (let left = 0; left < 4; left += 1) for (let right = left + 1; right < 4; right += 1) {
    const a = TETRAHEDRON_VERTICES[left]; const b = TETRAHEDRON_VERTICES[right];
    distances.add(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]).toFixed(6));
  }
  assert.deepEqual([...distances], [Math.sqrt(8).toFixed(6)]);
});

test('perspective projection stays finite, depth sorted, and amplitude responsive', () => {
  const quiet = projectTetrahedron(0.7, 0, 240);
  const loud = projectTetrahedron(0.7, 1, 240);
  assert.equal(quiet.length, 4);
  assert.ok(quiet.every((face, index) => Number.isFinite(face.depth) && (!index || quiet[index - 1].depth <= face.depth)));
  assert.ok(quiet.every(face => /^[-0-9., ]+$/.test(face.points)));
  assert.notDeepEqual(quiet.map(face => face.points), loud.map(face => face.points));
});
