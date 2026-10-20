export const PLANET_COLORS = ['#4D8DFF', '#A66BFF', '#FFB454', '#39D9A0', '#FF6FAE'];
export const COSMOS_BG = '#080D1D';
export const SATELLITE_COLOR = '#64748B';
export const EDGE_COLOR = '#334155';
export const SELECTED_TEXT = '#FFFFFF';
export const MAX_CENTERS = 5;

const DISTANCE_MIN = 260;
const DISTANCE_MAX = 560;
const DEFAULT_SIMILARITY = 0.5;
const GOLDEN_ANGLE = 137.5 * (Math.PI / 180);

export function toggleSelection(selected: string[], key: string): string[] | null {
  if (selected.includes(key)) return selected.filter(item => item !== key);
  if (selected.length >= MAX_CENTERS) return null;
  return [...selected, key];
}

export function parseSeeds(raw: string | null, valid: string[]): string[] {
  if (!raw) return [];
  const allowed = new Set(valid);
  const seen = new Set<string>();
  const result: string[] = [];
  for (const key of raw.split(',')) {
    if (!allowed.has(key) || seen.has(key)) continue;
    seen.add(key);
    result.push(key);
  }
  return result;
}

export function similarityDistance(sim: number | null): number {
  const value = sim ?? DEFAULT_SIMILARITY;
  return DISTANCE_MAX - value * (DISTANCE_MAX - DISTANCE_MIN);
}

export function planetPositions(sims: (number | null)[][]): { x: number; y: number }[] {
  const positions: { x: number; y: number }[] = [];
  for (let i = 0; i < sims.length; i += 1) {
    if (!positions.length) {
      positions.push({ x: 0, y: 0 });
      continue;
    }
    let nearest = 0;
    let nearestDistance = Number.POSITIVE_INFINITY;
    for (let j = 0; j < positions.length; j += 1) {
      const distance = similarityDistance(sims[i]?.[j] ?? null);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearest = j;
      }
    }
    const angle = i * GOLDEN_ANGLE;
    positions.push({
      x: positions[nearest].x + nearestDistance * Math.cos(angle),
      y: positions[nearest].y + nearestDistance * Math.sin(angle),
    });
  }
  return positions;
}

export function edgeDash(category: string): number[] {
  if (category === 'semantic') return [5, 4];
  if (category === 'keyword') return [2, 4];
  return [];
}

const ORBIT_RADIUS = 195;
const ORBIT_RADIUS_STEP = 80;

export function satelliteOrbit(center: { x: number; y: number }, centerIndex: number, slot: number): { x: number; y: number } {
  const angle = centerIndex * 0.6 + slot * (Math.PI / 3);
  const radius = ORBIT_RADIUS + Math.floor(slot / 6) * ORBIT_RADIUS_STEP;
  return { x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) };
}

export function graphBounds(planets: { x: number; y: number }[], orbitRadius = ORBIT_RADIUS + 30): { minX: number; minY: number; maxX: number; maxY: number } {
  const xs = planets.map((point) => point.x);
  const ys = planets.map((point) => point.y);
  return {
    minX: Math.min(...xs) - orbitRadius,
    minY: Math.min(...ys) - orbitRadius,
    maxX: Math.max(...xs) + orbitRadius,
    maxY: Math.max(...ys) + orbitRadius,
  };
}
