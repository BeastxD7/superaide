// Layout measurements for the phone-layout specs: what a person on a small screen would notice.
import type { Page } from '@playwright/test'

export type Measure = {
  vw: number
  scrollWidth: number
  wide: string[] // visible elements that stick out past the right edge
  small: string[] // interactive elements under 44px tall (or wide) that are not inline text links
  smallInputs: string[] // text fields under 16px (iOS zooms on focus)
  tiny: string[] // text under 12px
  navTall: string[] // navbar controls that wrap (taller than 44px + padding) or are under 44px
}

const label = (el: Element) => {
  const text = (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 30)
  return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}[${text}]`
}

export async function measure(page: Page, root = 'body'): Promise<Measure> {
  return page.evaluate(
    ({ root, labelSrc }) => {
      const lbl = new Function(`return (${labelSrc})`)() as (el: Element) => string
      const vw = document.documentElement.clientWidth
      const visible = (el: Element) => {
        const r = el.getBoundingClientRect()
        const s = getComputedStyle(el)
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0
      }
      // Inside a scroller of its own (overflow auto/scroll) things may extend: skip those.
      const insideScroller = (el: Element) => {
        for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
          const o = getComputedStyle(p).overflowX
          if ((o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') && p.getBoundingClientRect().right <= vw + 1) return true
        }
        return false
      }
      const all = Array.from(document.querySelectorAll(`${root} *`)).filter(visible)
      const wide = all
        .filter((el) => el.getBoundingClientRect().right > vw + 1 && !insideScroller(el) && !el.closest('[aria-hidden="true"]'))
        .map(lbl)
        .slice(0, 8)
      const inline = (el: Element) => {
        // A link inside a sentence is exempt from the target-size rule (WCAG 2.5.8 inline exception).
        // (a link that flows with the text around it: computed display is inline)
        return el.tagName === 'A' && getComputedStyle(el).display === 'inline'
      }
      const small = all
        .filter((el) => !el.matches('[tabindex="-1"]')) // not reachable by keyboard or screen reader: the sidebar's mouse drag-rail
        .filter((el) => el.matches('button, a[href], [role=button], [role=tab], [role=menuitem], input:not([type=hidden]), select, textarea, summary'))
        .filter((el) => {
          const r = el.getBoundingClientRect()
          // A checkbox or radio inside a label that is itself 44px tall: the whole label is the target.
          const lab = el.closest('label')?.getBoundingClientRect()
          if (el.matches('input[type=checkbox], input[type=radio]') && lab && lab.height >= 44) return false
          return (r.height < 43.5 || r.width < 43.5) && !inline(el) && !el.matches('.sr-only, [class*="sr-only"]')
        })
        .map((el) => `${lbl(el)} ${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`)
        .slice(0, 12)
      const smallInputs = all
        .filter((el) => el.matches('input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=file]), select, textarea'))
        .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16)
        .map((el) => `${lbl(el)} ${getComputedStyle(el).fontSize}`)
        .slice(0, 8)
      const tiny = all
        .filter((el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent || '').trim().length > 0))
        .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 12 && !el.closest('.sr-only'))
        .map((el) => `${lbl(el)} ${getComputedStyle(el).fontSize}`)
        .slice(0, 8)
      const navTall = Array.from(document.querySelectorAll('header nav a, header nav button'))
        .filter(visible)
        .filter((el) => {
          const r = el.getBoundingClientRect()
          const lh = parseFloat(getComputedStyle(el).lineHeight) || parseFloat(getComputedStyle(el).fontSize) * 1.4
          const textLines = el.textContent?.trim() ? Math.round(r.height / lh) : 1
          return r.height < 44 || (textLines > 2 && r.height > 70)
        })
        .map((el) => `${lbl(el)} ${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`)
      return { vw, scrollWidth: document.documentElement.scrollWidth, wide, small, smallInputs, tiny, navTall }
    },
    { root, labelSrc: label.toString() },
  )
}
