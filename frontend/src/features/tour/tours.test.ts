import { describe, expect, it } from 'vitest'
import { autoStart, fill, popoverPosition, spotlight, tourForPath, TOURS } from '@/features/tour/tours'

describe('autoStart', () => {
  it('asks once, on the first visit', () => {
    expect(autoStart({}, 'chat')).toBe('welcome')
  })
  it('never starts a page tour after the owner skipped the welcome', () => {
    expect(autoStart({ welcome: 'skipped' }, 'chat')).toBeNull()
  })
  it('starts the page tour once the owner took the tour, until it is finished or skipped', () => {
    expect(autoStart({ welcome: 'completed' }, 'chat')).toBe('chat')
    expect(autoStart({ welcome: 'completed', chat: 'completed' }, 'chat')).toBeNull()
    expect(autoStart({ welcome: 'completed', chat: 'skipped' }, 'chat')).toBeNull()
  })
  it('has nothing to start on a page without a tour', () => {
    expect(autoStart({ welcome: 'completed' }, null)).toBeNull()
  })
})

describe('tourForPath', () => {
  it('maps the pages that have a tour', () => {
    expect(tourForPath('/dashboard')).toBe('home')
    expect(tourForPath('/dashboard/chat')).toBe('chat')
    expect(tourForPath('/dashboard/settings')).toBe('settings')
    expect(tourForPath('/dashboard/people')).toBeNull()
  })
})

describe('copy', () => {
  it('fills the agent name and keeps no placeholder or real-person name', () => {
    expect(fill('Meet {name}', 'Ava')).toBe('Meet Ava')
    const all = JSON.stringify(TOURS)
    expect(all).not.toMatch(/Shashank/i)
    for (const t of Object.values(TOURS)) for (const s of t.steps) expect(fill(s.body, 'Ava')).not.toContain('{')
  })
})

describe('geometry', () => {
  it('keeps the spotlight inside the viewport', () => {
    expect(spotlight({ top: 2, left: 2, width: 100, height: 40 }, 8, 400, 800)).toEqual({ top: 0, left: 0, width: 110, height: 50 })
    expect(spotlight({ top: 780, left: 380, width: 100, height: 40 }, 8, 400, 800)).toEqual({ top: 772, left: 372, width: 28, height: 28 })
  })
  it('puts the popover below, else above, and inside the screen', () => {
    const pop = { width: 320, height: 160 }
    expect(popoverPosition({ top: 100, left: 20, width: 80, height: 40 }, pop, 1000, 800)).toEqual({ top: 152, left: 8 })
    const above = popoverPosition({ top: 700, left: 500, width: 80, height: 40 }, pop, 1000, 800)
    expect(above.top).toBe(528)
    const phone = popoverPosition({ top: 100, left: 300, width: 40, height: 40 }, { width: 330, height: 160 }, 360, 700)
    expect(phone.left).toBe(22)
  })
})
