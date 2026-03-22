import { describe, it, expect, vi, beforeEach } from 'vitest'

// Photon-format FeatureCollection response
const mockPhotonResponse = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: [-0.1276, 51.5074],
      },
      properties: {
        name: 'London',
        country: 'United Kingdom',
        osm_key: 'place',
        osm_value: 'city',
      },
    },
  ],
}

describe('forwardGeocode', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockPhotonResponse),
      statusText: 'OK',
    })
  })

  it('calls Photon API with the query parameter', async () => {
    const { forwardGeocode } = await import('@/lib/server/services/geocoding')

    await forwardGeocode('london')

    expect(globalThis.fetch).toHaveBeenCalledOnce()
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain('q=london')
  })

  it('returns a FeatureCollection with features', async () => {
    const { forwardGeocode } = await import('@/lib/server/services/geocoding')

    const result = await forwardGeocode('london')

    expect(result.type).toBe('FeatureCollection')
    expect(result.features).toHaveLength(1)
  })

  it('returns feature with correct coordinates', async () => {
    const { forwardGeocode } = await import('@/lib/server/services/geocoding')

    const result = await forwardGeocode('london')

    expect(result.features[0].geometry.coordinates).toEqual([-0.1276, 51.5074])
  })

  it('throws on non-ok response', async () => {
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      statusText: 'Service Unavailable',
    })

    const { forwardGeocode } = await import('@/lib/server/services/geocoding')

    await expect(forwardGeocode('london')).rejects.toThrow('Geocoding failed')
  })
})

describe('normalizeResults', () => {
  it('normalizes a Photon FeatureCollection into NormalizedGeocodingResult[]', async () => {
    const { normalizeResults } = await import('@/lib/server/services/geocoding')

    const results = normalizeResults(mockPhotonResponse as Parameters<typeof normalizeResults>[0])

    expect(results).toHaveLength(1)
    expect(results[0]).toHaveProperty('id')
    expect(results[0]).toHaveProperty('coordinates')
    expect(results[0]).toHaveProperty('displayName')
    expect(results[0].coordinates).toEqual([-0.1276, 51.5074])
  })

  it('resolves type to "city" for a city feature', async () => {
    const { normalizeResults } = await import('@/lib/server/services/geocoding')

    const results = normalizeResults(mockPhotonResponse as Parameters<typeof normalizeResults>[0])

    expect(results[0].type).toBe('city')
  })

  it('returns features array', async () => {
    const { normalizeResults } = await import('@/lib/server/services/geocoding')

    const results = normalizeResults(mockPhotonResponse as Parameters<typeof normalizeResults>[0])

    expect(Array.isArray(results)).toBe(true)
  })
})
