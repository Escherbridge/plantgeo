import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock database dependency so importing the router doesn't require a live DB
vi.mock('@/lib/server/db', () => ({
  db: {
    select: vi.fn(() => ({
      from: vi.fn(() => ({
        where: vi.fn(() => Promise.resolve([])),
      })),
    })),
    insert: vi.fn(() => ({
      values: vi.fn(() => ({
        returning: vi.fn(() => Promise.resolve([])),
      })),
    })),
    update: vi.fn(() => ({
      set: vi.fn(() => ({
        where: vi.fn(() => ({
          returning: vi.fn(() => Promise.resolve([])),
        })),
      })),
    })),
    delete: vi.fn(() => ({
      where: vi.fn(() => Promise.resolve()),
    })),
  },
}))

// Mock auth session so procedures don't fail auth checks
vi.mock('@/lib/server/auth', () => ({
  auth: vi.fn(() => Promise.resolve(null)),
  getSession: vi.fn(() => Promise.resolve(null)),
}))

import { layersRouter } from '@/lib/server/trpc/routers/layers'

describe('layersRouter', () => {
  it('is defined', () => {
    expect(layersRouter).toBeDefined()
  })

  it('has expected procedures', () => {
    const procedures = Object.keys(layersRouter._def.procedures)
    expect(procedures).toContain('list')
    expect(procedures).toContain('getById')
    expect(procedures).toContain('create')
    expect(procedures).toContain('update')
    expect(procedures).toContain('delete')
    expect(procedures).toContain('reorder')
  })

  it('exposes all 6 procedures', () => {
    const procedures = Object.keys(layersRouter._def.procedures)
    expect(procedures).toHaveLength(6)
  })
})
