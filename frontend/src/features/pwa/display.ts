// Is the app running from the Home Screen (installed), and on which kind of device?

type NavigatorWithStandalone = Navigator & { standalone?: boolean }

export function isStandalone(win: Window = window): boolean {
  return Boolean(
    win.matchMedia?.('(display-mode: standalone)').matches ||
      win.matchMedia?.('(display-mode: fullscreen)').matches ||
      (win.navigator as NavigatorWithStandalone).standalone,
  )
}

export function isIOS(nav: Navigator = navigator): boolean {
  // iPadOS reports itself as a Mac; a Mac has no touch points.
  return /iPhone|iPad|iPod/.test(nav.userAgent) || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1)
}

export type DevicePlatform = 'ios' | 'ipados' | 'android' | 'desktop' | 'other'

export function platformOf(nav: Navigator = navigator): DevicePlatform {
  if (/iPhone|iPod/.test(nav.userAgent)) return 'ios'
  if (isIOS(nav)) return 'ipados'
  if (/Android/.test(nav.userAgent)) return 'android'
  if (/Windows|Macintosh|Linux|CrOS/.test(nav.userAgent)) return 'desktop'
  return 'other'
}
