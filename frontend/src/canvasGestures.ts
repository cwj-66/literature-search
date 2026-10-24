export interface CanvasCamera { x: number; y: number; k: number }
export interface CanvasPoint { x: number; y: number }

/** Keep the graph point under the fingers fixed while their midpoint moves. */
export function transformGesture(camera: CanvasCamera, before: CanvasPoint[], after: CanvasPoint[], min: number, max: number): CanvasCamera {
  if (!before.length || before.length !== after.length) return camera;
  if (before.length === 1) return { ...camera, x: camera.x + after[0].x - before[0].x, y: camera.y + after[0].y - before[0].y };
  const midpoint = (p: CanvasPoint[]) => ({ x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 });
  const distance = (p: CanvasPoint[]) => Math.hypot(p[1].x - p[0].x, p[1].y - p[0].y);
  const a = midpoint(before), b = midpoint(after), d = distance(before);
  const k = Math.max(min, Math.min(max, camera.k * (d > 0 ? distance(after) / d : 1)));
  return { k, x: b.x - (a.x - camera.x) * k / camera.k, y: b.y - (a.y - camera.y) * k / camera.k };
}
