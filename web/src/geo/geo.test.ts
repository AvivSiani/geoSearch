import type { Polygon } from 'geojson';
import { describe, expect, it } from 'vitest';
import { circleToPolygon } from './circle';
import { formatLatLon, parseCoordinates, wrapLon } from './coords';
import { areaKm2, findAreaIssue } from './measure';
import { thumbnailPath } from './thumbnail';
import { toWkt } from './wkt';

const square: Polygon = {
  type: 'Polygon',
  coordinates: [
    [
      [34.75, 32.05],
      [34.8, 32.05],
      [34.8, 32.1],
      [34.75, 32.1],
      [34.75, 32.05],
    ],
  ],
};

describe('toWkt', () => {
  it('writes lon-lat order, matching the README example', () => {
    expect(toWkt(square)).toBe('POLYGON ((34.75 32.05, 34.8 32.05, 34.8 32.1, 34.75 32.1, 34.75 32.05))');
  });

  it('closes an open ring', () => {
    const open: Polygon = { type: 'Polygon', coordinates: [square.coordinates[0]!.slice(0, -1)] };
    expect(toWkt(open)).toBe(toWkt(square));
  });

  it('keeps full precision and never writes exponents or -0', () => {
    const p: Polygon = {
      type: 'Polygon',
      coordinates: [[[34.123456789012, 32.987654321098], [-0, 1e-7], [1, 1], [34.123456789012, 32.987654321098]]],
    };
    expect(toWkt(p)).toBe('POLYGON ((34.123456789012 32.987654321098, 0 0.0000001, 1 1, 34.123456789012 32.987654321098))');
  });

  it('writes multipolygons', () => {
    expect(toWkt({ type: 'MultiPolygon', coordinates: [square.coordinates] })).toMatch(/^MULTIPOLYGON \(\(\(34\.75 32\.05/);
  });
});

describe('measure', () => {
  it('computes km² close to the backend for the README polygon', () => {
    // ~4.7 km × 5.55 km
    expect(areaKm2(square)).toBeGreaterThan(25);
    expect(areaKm2(square)).toBeLessThan(27);
  });

  it('accepts a valid area under the limit', () => {
    expect(findAreaIssue(square, 100)).toBeNull();
  });

  it('flags an area over the limit', () => {
    expect(findAreaIssue(square, 10)).toMatchObject({ kind: 'too_large', maxKm2: 10 });
  });

  it('flags a self-intersecting bow-tie', () => {
    const bowtie: Polygon = { type: 'Polygon', coordinates: [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]] };
    expect(findAreaIssue(bowtie, 1e9)).toEqual({ kind: 'self_intersects' });
  });

  it('flags degenerate rings', () => {
    const line: Polygon = { type: 'Polygon', coordinates: [[[0, 0], [1, 1], [0, 0]]] };
    expect(findAreaIssue(line, 100)).toEqual({ kind: 'too_few_points' });
  });
});

describe('circleToPolygon', () => {
  it('produces a closed ring whose area matches πr²', () => {
    const poly = circleToPolygon({ lat: 32.08, lon: 34.78 }, 1000, 128);
    const ring = poly.coordinates[0]!;
    expect(ring).toHaveLength(129);
    expect(ring[0]).toEqual(ring[128]);
    expect(areaKm2(poly)).toBeCloseTo(Math.PI, 1);
    expect(findAreaIssue(poly, 100)).toBeNull();
  });
});

describe('coordinates', () => {
  it('formats with hemispheres', () => {
    expect(formatLatLon({ lat: 32.0853, lon: 34.7818 })).toBe('32.0853° N, 34.7818° E');
    expect(formatLatLon({ lat: -1.5, lon: -70.25 })).toBe('1.5000° S, 70.2500° W');
  });

  it.each([
    ['32.08, 34.78', { lat: 32.08, lon: 34.78 }],
    ['32.08 34.78', { lat: 32.08, lon: 34.78 }],
    ['32.0853° N, 34.7818° E', { lat: 32.0853, lon: 34.7818 }],
    ['34.78 E 32.08 N', { lat: 32.08, lon: 34.78 }],
    ['10 S, 20 W', { lat: -10, lon: -20 }],
  ])('parses %s', (text, expected) => {
    expect(parseCoordinates(text)).toEqual(expected);
  });

  it.each(['תל אביב', '32.08', '95, 10', 'a 1 2', '1, 2, 3'])('rejects %s', (text) => {
    expect(parseCoordinates(text)).toBeNull();
  });

  it('wraps longitudes', () => {
    expect(wrapLon(190)).toBeCloseTo(-170);
    expect(wrapLon(-190)).toBeCloseTo(170);
    expect(wrapLon(34.78)).toBeCloseTo(34.78);
  });
});

describe('thumbnailPath', () => {
  it('fits the shape in the box', () => {
    const d = thumbnailPath(square, 46);
    const nums = d.match(/-?\d+(\.\d+)?/g)!.map(Number);
    expect(Math.min(...nums)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...nums)).toBeLessThanOrEqual(46);
    expect(d.endsWith('Z')).toBe(true);
  });
});
