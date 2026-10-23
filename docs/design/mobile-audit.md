# Mobile layout audit (before the responsive work)

Measured with a throwaway Playwright script (Pixel 7 emulation, signed-out and a signed-in owner on a fake-model stack) at 320, 360, 390, 430 and 768 px. Method: `e2e/src/helpers/responsive.ts` measures sideways scroll, elements past the right edge, tap targets under 44px, inputs under 16px, text under 12px and wrapped navbar controls. 1024 and 1440 showed no overflow. Screenshots live on the `evidence` branch under `pr-mobile-*`.

| Page | Widths | Defect | Evidence |
|---|---|---|---|
| `/` | 320, 360, 390, 430 | Page scrolls sideways (scrollWidth > viewport); elements stick out: ['div[Log inMeet Ava]', 'button[Meet Ava]'] | shots/home-390-full.png |
| `/` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Superaide] 118x28', 'a[Agents] 77x32', 'a[How hiring works] 143x32', 'a[Safety] 74x32'] | |
| `/` | 320, 360, 390, 430, 768 | Navbar controls wrap or are under 44px: ['a[Superaide] 118x28', 'button[Meet Ava] 112x72'] | |
| `/agents/ava` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Superaide] 118x28', 'a[Log in] 72x40', 'a[All agents] 100x28'] | |
| `/agents/ava` | 320, 360, 390, 430, 768 | Navbar controls wrap or are under 44px: ['a[Superaide] 118x28', 'a[Log in] 72x40'] | |
| `/login` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Back] 40x40', 'a[Superaide] 66x20', 'button[Show password] 36x36', 'a[Forgot your password?] 150x20'] | |
| `/signup` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Back] 40x40', 'a[Superaide] 66x20', 'button[Show password] 36x36'] | |
| `/forgot-password` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Superaide] 66x20', 'a[Back to log in] 90x20'] | |
| `/reset-password?token=abc` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Superaide] 66x20', 'a[Ask for a new link] 114x18'] | |
| `/verify-email` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Superaide] 66x20'] | |
| `/agents/ava/hire` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Back] 40x40', 'button#radix-_r_0_[Account: signed in as Ananya] 40x40', 'input[] 19x20'] | |
| `/meet` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['button#radix-_r_0_[Account: signed in as Ananya] 40x40', 'a[Leave for now] 36x36'] | |
| `/dashboard` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/chat` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/notebook` | 768 | Inputs under 16px (iOS zooms on focus): ['input[Search the notebook] 14px'] | |
| `/dashboard/notebook` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/reminders` | 768 | Inputs under 16px (iOS zooms on focus): ['input#new-reminder[] 14px'] | |
| `/dashboard/reminders` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/approvals` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/people` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/connections` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/models` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/capabilities` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/settings` | 768 | Inputs under 16px (iOS zooms on focus): ['input#new-email[] 14px', 'input#email-password[] 14px'] | |
| `/dashboard/settings` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |
| `/dashboard/skills` | 320, 360, 390, 430, 768 | Tap targets under 44px, e.g. ['a[Home] 224x32', 'a[Chat] 224x32', 'a[Reminders] 224x32', 'a[Notebook] 224x32'] | |

Status: shared navbar/Button/Input/Sheet and the home page overflow are fixed in the first PR; marketplace, auth and onboarding in the second; dashboard in the third.
