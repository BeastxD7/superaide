# 0011: Mobile (an installable web app first, native only if needed)

Status: proposed (2026-10-22). Design only; nothing here is built yet.

## Context

The owner asked about a mobile app for Ava. The product vision (`.delivery/product-vision.md`, "Phone and
mobile") says that no native app was ever designed, and it sketches what one would be:

- It is the **owner's** app. Contacts keep using their own channels (Telegram, Slack, the web chat).
- The core is: what Ava handled and what needs the owner; interrupts with Acknowledge, Reply and Snooze;
  an approvals queue; the digest; and "tell Ava" by voice.
- Push becomes the first rung of the escalation ladder, using iOS Time Sensitive for urgent items and
  respecting quiet hours.
- The order is: triage and escalation first, then a PWA with web push, then native (for example Expo
  sharing the TypeScript API client) only if the PWA isn't enough.
- It names three technical gaps for native: cookie-plus-CSRF auth that only suits browsers, no endpoint
  to register push tokens, and lock-screen previews that must follow the "decided in code" rule.

This ADR turns that sketch into a decision. It builds on ADR 0009 (every row is scoped to an agent
instance, with RLS as the second line of defence) and ADR 0010 (the ladder, acks and the Needs-you inbox).

### What the code does today

Frontend paths are relative to `frontend/src/` and backend paths to `backend/src/superaide/`.

| Where | Today | Gap for mobile |
|---|---|---|
| `app/(site)/dashboard/*`, `features/dashboard/Shell.tsx`, `AppSidebar.tsx` | Chat, Approvals, Reminders, Notebook, People and others. On a phone the sidebar opens as a sheet (`e2e/tests/workspace.mobile.spec.ts` checks Pixel 7 layout, no sideways scroll, composer in view). | There is no Needs-you page yet (that is ADR 0010 TR9) and no digest page. Every page is one tap behind the sheet. |
| `app/` metadata | `icon.svg`, `apple-icon.png`, `favicon.ico` | No web app manifest, no service worker, no install path. |
| `lib/http.ts`, `app/api/[...path]/route.ts` | The browser calls same-origin `/api/*` and Next.js forwards it to FastAPI (ADR 0005). Every request sends `X-Requested-With: superaide`. `ALLOWED` = `auth`, `app`, `api`, `health`. | Fine for a PWA. A native client can't use this cookie flow comfortably. |
| `proxy.ts` | Redirects signed-out visitors from `/dashboard/*`, `/meet`, `/workspace` and hire pages | `sw.js` and the manifest must stay outside its matcher (they already are). |
| `next.config.ts` | Security headers. `Permissions-Policy: camera=(self), microphone=(self)` | No headers for `sw.js`. |
| `features/onboarding/speech.ts` | Web Speech API. Its header says that in Chrome the audio goes to Google's speech service. Brave has no recognition at all (the vision's "Brave problem"). | "Tell Ava" has no on-device path. |
| `auth/routes.py`, `auth/service.py` (ADR 0003) | A 256-bit opaque session token in an httpOnly, SameSite=Lax cookie, stored as SHA-256 with a 30-day sliding expiry. `require_csrf_header` rejects unsafe methods without `X-Requested-With`. No JWTs. | No bearer-token login, no refresh, no device record. |
| `proactive/notify.py` `notify_owner` | Writes to the app and optionally sends to Telegram (Slack only when named) | There is no push provider. ADR 0010 TR3 replaces the one-shot send with the ladder. |
| `db/models.py` `OwnerPreference` | `interrupt_channel` (app, telegram or slack), `quiet_start`/`quiet_end`, `vip_breaks_quiet` | `push` is not a channel. |
| `vault/` | AES-256-GCM under a Keychain master key. ADR 0009 §6 adds per-instance AAD and a `platform_secret` table for platform credentials. | No VAPID keys. |

### Research (October 2026)

**PWA on iOS and iPadOS**

- Web Push and the Badging API arrived in iOS/iPadOS 16.4 **for Home Screen web apps only**. In a Safari
  tab there is no push. Permission must be requested after a user gesture. The manifest's `display` must
  be `standalone` or `fullscreen`. No Apple Developer Program membership is needed. Servers must allow
  `*.push.apple.com`. Notifications follow Focus, and Focus settings sync by manifest `id`
  ([WebKit](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)).
- **Declarative Web Push** (iOS/iPadOS 18.4, macOS in Safari 18.5): a JSON payload with
  `"web_push": 8030`, a `notification` object (a non-empty `title` is required, plus `body`, `lang`,
  `silent`, and `app_badge`) and a required `navigate` URL. It works with no service worker. If a service
  worker exists, it can replace the notification, and the declarative one is the fallback if the script
  fails. It is backwards compatible with browsers that only do the imperative flow
  ([WebKit](https://webkit.org/blog/16535/meet-declarative-web-push/),
  [Safari 18.4](https://webkit.org/blog/16574/webkit-features-in-safari-18-4/)).
- **iOS 26**: every site added to the Home Screen opens as a web app by default, with an "Open as web app"
  toggle in the sheet ([heise](https://heise.de/-10749652), [WebKit WWDC25](https://webkit.org/?p=16993)).
- **Notification action buttons are not supported in Safari or Safari iOS** (MDN browser-compat-data:
  `Notification.actions` is `false` for `safari` and `safari_ios`;
  [BCD](https://github.com/mdn/browser-compat-data/blob/main/api/Notification.json)). A developer on iOS
  16.4 saw only the default "View" action
  ([Apple forums](https://developer.apple.com/forums/thread/726793)). On iOS, **tapping is the only
  interaction**.
- **Silent pushes are punished.** `userVisibleOnly: true` is required, and after three pushes that don't
  show a notification, WebKit removes the subscription
  ([WebKit commit](https://webkit.googlesource.com/WebKit/+/108ccb442e40e9705d212e74c1f213e3b5a0ea05),
  [Meet Web Push](https://webkit.org/?p=12945),
  [progressier](https://dev.to/progressier/how-to-fix-ios-push-subscriptions-being-terminated-after-3-notifications-39a7)).
  `pushsubscriptionchange` is not fired on iOS
  ([WebKit bug 273063](https://bugs.webkit.org/show_bug.cgi?id=273063)). Apple's push endpoint can
  answer success for a subscription that is no longer valid
  ([Apple forums](https://developer.apple.com/forums/thread/719990)).
- **Time Sensitive.** For native apps it is a payload `interruption-level` plus a capability, and it
  breaks through Focus. Critical alerts need an Apple entitlement
  ([OneSignal](https://documentation.onesignal.com/v9.0/docs/ios-focus-modes-and-interruption-levels)).
  **We found no evidence that web push can set an interruption level**, and the WebKit posts don't
  mention one. Treat it as unavailable to a PWA: on iOS, Focus holds Ava's web pushes unless the owner
  allows Ava in that Focus.
- **Storage.** Safari's 7-day cap on script-writable storage does not apply to Home Screen web apps,
  which have their own counter based on actual use. Apple called deletion in a Home Screen app "a serious
  bug" ([Search Engine Land](https://searchengineland.com/what-safaris-7-day-cap-on-script-writeable-storage-means-for-pwa-developers-332519)).
  There is still a report of Home Screen app users being logged out after 7 days
  ([WebKit bug 237350](https://bugs.webkit.org/show_bug.cgi?id=237350)). The Home Screen app has its own
  storage and cookies, separate from Safari, so the owner signs in once inside the installed app.
- **EU.** Apple removed Home Screen web apps for the EU in the iOS 17.4 betas, then reversed that and
  kept them, built on WebKit
  ([TechCrunch](https://techcrunch.com/?p=2666084),
  [ComputerBase](https://www.computerbase.de/2024-03/dma-apple-deaktiviert-progressive-web-apps-mit-ios-17-4-doch-nicht/)).
  Secondary sources still disagree on whether push works there. **Unverified: test on an EU-region
  device.**

**PWA on Android (Chrome)**

- Notification `actions` have been supported since Chrome 53, and `Notification.maxActions` since 48
  ([BCD](https://github.com/mdn/browser-compat-data/blob/main/api/Notification.json)). Android shows
  two buttons in practice, so two is our limit.
- Chrome on Android doesn't support the Badging API. An installed app gets the platform's dot when it has
  an unread notification ([Chrome](https://developer.chrome.com/docs/capabilities/web-apis/badging-api)).
- Web push from Chrome can't set Android's lock-screen visibility or a public version. Android's default
  is `VISIBILITY_PRIVATE` behaviour, governed by the user's per-device and per-channel settings
  ([AOSP](https://gitlab.e.foundation/e/os/android_frameworks_base/-/commit/0bf2ed8ae394b327af1bc6e853a698c2dac67aaa)).
  **For a PWA, what we put in the payload is the only privacy control we have.**

**Native options for sharing our TypeScript**

- **Expo / React Native** (SDK 55 = React Native 0.83 + React 19.2, New Architecture only) renders native
  views. It shares TypeScript logic such as the API client, types and validation, but not our
  DOM/Tailwind/Radix components
  ([pkgpulse](https://www.pkgpulse.com/guides/react-native-vs-expo-vs-capacitor-cross-platform-mobile-2026)).
- **Capacitor 8** wraps a web app in a WebView. This Next.js app has server parts (`proxy.ts`,
  `app/api/[...path]/route.ts`), so a static export is out. Capacitor would load the hosted URL, which is
  the pattern App Review rejects under 4.2. A Capacitor app with push, Core Location and sharing was
  still rejected as "not sufficiently different from a mobile browsing experience"
  ([Apple forums](https://developer.apple.com/forums/thread/812889),
  [MobiLoud](https://www.mobiloud.com/blog/app-store-review-guidelines-webview-wrapper)).
- **Every reason we'd go native is a native-first feature**: Time Sensitive, action buttons on iOS,
  widgets, Siri/App Intents, a share-sheet "tell Ava", and on-device speech. A WebView shell gains little
  over the PWA for those.

**Push providers**

- **Self-hosted Web Push with VAPID** (RFC 8030/8291/8292). Payloads are end-to-end encrypted to the
  browser (aes128gcm), so Apple, Google and Mozilla see ciphertext, size and timing only. It is free.
  Python libraries are `pywebpush` (VAPID, aes128gcm, sync and async) and the smaller typed `webpush`
  package ([pywebpush](https://github.com/web-push-libs/pywebpush), [webpush](https://pypi.org/project/webpush)).
- **APNs and FCM directly**: needed only for native. APNs uses a token-based `.p8` key over HTTP/2, and
  FCM uses the HTTP v1 API with a service account.
- **Expo push**: free, limited to 600 notifications per second per project, and a relay in front of
  APNs/FCM ([Expo FAQ](https://docs.expo.dev/push-notifications/faq/)). Expo's servers see the payload.
  Native device tokens can be used without it.

**Store policy (App Store Review Guidelines, fetched 2026-10-22,
[Apple](https://developer.apple.com/app-store/review/guidelines/))**

- **4.5.4**: "Push Notifications … should not be used to send sensitive personal or confidential
  information." This supports content-free previews.
- **4.2**: the app needs "features, content, and UI that elevate it beyond a repackaged website."
- **5.1.1(v)**: if the app supports account creation, it must offer account deletion in the app.
- **5.1.2(i)** (revised 13 Nov 2025): "You must clearly disclose where personal data will be shared with
  third parties, including with third-party AI, and obtain explicit permission before doing so." That
  means a consent screen naming the model provider (Anthropic, or the owner's own provider under ADR
  0007).
- **1.2 / 4.7 / 4.7.1** (AI content creation apps and chatbots): filtering, "a mechanism to report
  offensive content and timely responses", the ability to block users, and published contact details.
- **Messaging on the user's behalf**: no guideline covers it directly. The closest are **4.5.3** (no spam
  or unsolicited messages through Apple services) and **1.1.6** (no anonymous or prank calls or SMS).
  Ava sends from the server, through Telegram, Slack or web, after the owner's approval. The app itself
  sends nothing to contacts.
- **3.1.1 / 3.1.3(b)**: features unlocked in the app need in-app purchase. Multiplatform services may
  unlock what was bought on the web "provided those items are also available as in-app purchases". The
  **3.1.3(f)** "free stand-alone companion" exemption names only VoIP, cloud storage, email and web
  hosting. The US storefront allows external purchase links. **IAP exposure is an open question, not a
  verdict.**
- **Google Play**: the AI-Generated Content policy requires in-app reporting or flagging of offensive AI
  output ([Play](https://support.google.com/googleplay/android-developer/answer/14094294)). Ava needs no
  SMS or Call Log permissions, which are restricted on Play, because she doesn't send from the phone.

**Voice ("tell Ava")**

- Web: the Web Speech API is gaining `processLocally`, `SpeechRecognition.available()` and `install()`
  (a language pack, which needs a user gesture)
  ([MDN](https://developer.mozilla.org/docs/Web/API/SpeechRecognition/available_static)). Edge 150 lists
  on-device languages ([Microsoft](https://learn.microsoft.com/fi-fi/microsoft-edge/web-platform/speech-recognition-api)).
  **The Chrome rollout status is unverified**, so feature-detect it. The keyboard's own dictation key
  works in every input on iOS and Android at no cost to us.
- Native: `SpeechAnalyzer`/`SpeechTranscriber` in iOS 26 runs on the device, with a system-managed model,
  and `DictationTranscriber` covers other languages
  ([WWDC25 277](https://developer.apple.com/videos/play/wwdc2025/277/),
  [addpipe](https://blog.addpipe.com/apple-speechanalyzer-api/)). Android has
  `SpeechRecognizer.createOnDeviceSpeechRecognizer` (API 31+).

**Lock-screen previews**

- iOS has one "Show Previews" setting per app (Always, When Unlocked, Never), and notifications mirror to
  a paired Apple Watch. Android hides "sensitive content" per device or channel. Other apps with
  notification access (wearables, automation apps) can read notification text. Notification Center keeps
  history. The owner may change any of these settings, and we can't see them. So **the server must
  assume the lock screen is public.**

## Decision

### 1. The recommendation and phasing

1. **Phase 1, now: the PWA.** Make the existing Next.js app installable and mobile-first, and add Web
   Push as rung 0 of the ADR 0010 ladder. This costs no store fees, no review and no second codebase.
   Everything that decides identity, audience and policy stays in the backend, unchanged.
2. **Phase 2, only if needed: native, with Expo.** Go native only when one of the measured triggers in
   section 8 fires. Expo shares an extracted `@superaide/api-client` package; Capacitor is not chosen
   (see Research).

The app is the **owner's**. Nothing in it is for contacts. Sign-in, onboarding and settings stay as
they are; the mobile work makes five things first-class on a phone.

### 2. Mobile-first screens (PWA)

| Screen | Route (ADR 0009 adds `/w/{instance}` later) | Source | Mobile behaviour |
|---|---|---|---|
| **Needs you** | `/dashboard/needs-you` | ADR 0010 TR9 (owned there) | Default landing page of the installed app. Each item has big **Acknowledge**, **Reply**, **Snooze 30m/2h** and **Not urgent** buttons. Opening an item never counts as an ack (0010 §5). |
| **Chat** | `/dashboard/chat` | exists | Composer pinned above the keyboard (`visualViewport`), with a mic button for "tell Ava" (section 7) |
| **Approvals** | `/dashboard/approvals` (new route; today `ApprovalsCard` sits on the `/dashboard` home) | exists (`Approvals.tsx`) | Its own tab: one card per approval, Approve/Decline at thumb height |
| **Reminders** | `/dashboard/reminders` | exists | Unchanged; reminders can come due as low-urgency pushes (section 4) |
| **Digest** | `/dashboard/digest` (new) | `proactive/digest.py` output, read from the owner's events | The latest digest and the previous 7, in 0010 §10's section order |

- **Navigation.** Below the `md` breakpoint, a bottom tab bar shows Needs you (with a count badge), Chat,
  Approvals and More. More opens today's sidebar sheet. Desktop is unchanged.
- **Install path.**
  - On Android, show our own "Install Ava" button driven by `beforeinstallprompt`.
  - On iOS Safari, when the app isn't running standalone (`matchMedia('(display-mode: standalone)')`
    or `navigator.standalone`), show a one-time sheet: "Share, then Add to Home Screen".
  - Push is only offered **inside** the installed app, behind an explicit "Turn on notifications" tap
    (the user-gesture rule).
- **Manifest** (`app/manifest.ts`):
  - `id: "/dashboard"` (stable, so Focus settings sync), `start_url: "/dashboard/needs-you"`,
    `display: "standalone"`, `scope: "/"`;
  - `name: "Ava"` (what the owner hired), with "Superaide" in `description`;
  - 192 and 512 px icons plus maskable versions;
  - theme and background colours from the app tokens.

  `start_url` can't be per-instance, so `/dashboard` redirects to the owner's instance (0009 §2).
- **Service worker** (`public/sw.js`, plain JS, no build step). It handles only `push`,
  `notificationclick` and `notificationclose`.
  - **It caches no API responses and no personal data.** At most it precaches the static app shell
    (hashed `/_next/static/*` files) for a fast start. Offline shows "You're offline. Ava keeps
    working; this page will refresh."
  - Every `push` handler wraps `showNotification` in `event.waitUntil` (the three-strike rule).
  - `next.config.ts` serves `/sw.js` with `Cache-Control: no-cache, no-store, must-revalidate`,
    `Content-Type: application/javascript` and `Service-Worker-Allowed: /`.
- **Re-subscribe on every open.** iOS doesn't fire `pushsubscriptionchange`. So each time the installed
  app starts, it calls `pushManager.getSubscription()` and upserts the result to the API, which updates
  `last_seen_at`. If a subscription has vanished, the app shows a banner: "Notifications are off on
  this device. Turn on."

### 3. Push design

#### 3.1 Data model (per instance, ADR 0009)

New module `push/models.py`, registered with one import line in the metadata registry, like
`triage/models.py` in 0010 §7. All tables use `Scoped` (`tenant_id`, `agent_id`), so T1's RLS policies
and meta-test cover them. Indexes lead with `agent_id`.

```
push_subscription  id, tenant_id, agent_id,
                   user_id → user_account, person_id → person (the instance owner),
                   session_id → user_session null          -- web: device binding (3.6)
                   device_id → user_device null            -- native: bound to the device, not to the
                                                           -- 15-minute session rows (section 5)
                   kind (webpush | apns | fcm),            -- apns/fcm only in phase 2
                   endpoint_hash char(64)                  -- sha256 of endpoint or device token
                   secret_enc bytea                        -- AES-256-GCM of {endpoint, p256dh, auth}
                                                           -- or the device token; AAD "{agent_id}/push/{id}"
                   push_host text                          -- e.g. web.push.apple.com, fcm.googleapis.com (not secret)
                   device_label text                       -- "iPhone", editable ("Shashank's iPhone")
                   platform (ios | ipados | android | desktop | other)
                   preview_level (none | sender | reason)  -- default none (3.3)
                   muted_until timestamptz null            -- per-device mute
                   enabled bool, created_at, last_seen_at, last_success_at,
                   last_failure_at, failure_count int, gone_at timestamptz null
                   UNIQUE (agent_id, endpoint_hash)

push_send          id, tenant_id, agent_id, delivery_id → escalation_delivery null,
                   subscription_id → push_subscription, purpose (escalation | digest | reminder | test),
                   status (sent | failed | gone), http_status int, error text, at
                   UNIQUE (delivery_id, subscription_id)     -- one attempt row per device per rung
```

- **One device, several instances.** A device that belongs to an owner of two instances (an org, later)
  gets one row per instance with the same `endpoint_hash`. Rows are never shared across instances.
- **The endpoint and keys are secrets.** Anyone who holds them can push to the device, so they are
  encrypted with the vault's AES-GCM under the 0009 §6 AAD pattern. A row copied to another instance
  fails to decrypt.
- **Amendment to ADR 0010 §7.**
  - `escalation_delivery` is unique on `(escalation_id, step, provider)`. Push therefore writes **one**
    delivery row with `provider = 'push'`, holding the opaque token, and fans out to the devices through
    `push_send`. A retry skips devices whose `push_send` row is already `sent`.
  - `escalation_ack.source` gains `push`.
- **Audit.** `push_subscribed`, `push_unsubscribed`, `push_gone`, `push_sent`/`push_failed` (with
  counts) and `escalation_acked` (`via = push`). The audit holds ids and labels, never payload text.

#### 3.2 VAPID keys

- A VAPID key pair is bound to the **origin's** `applicationServerKey`, and every subscription on that
  origin is made with it. It is therefore **one pair per deployment, not per instance.**
- The brief said "VAPID keys in the vault". They go in the vault's **`platform_secret`** table (0009 §6),
  so request code for an instance can't read them. Only `push/send.py` reads them, through the platform
  code path.
- `superaide push-keys --generate` creates the pair once (P-256). The public key is served at
  `GET /app/push/key`. `VAPID_SUBJECT` is a `mailto:` address in `.env.example`.
- **Rotating the pair invalidates every subscription.** Rotation is therefore a deliberate operation:
  generate, then mark all subscriptions `gone`, then ask each app to re-subscribe on its next open.
- A self-hosted deployment generates its own pair.

#### 3.3 Payloads obey the audience gate

**The lock screen is treated as the `public` audience.** The owner is the addressee, but the screen can
be read by anyone holding or near the phone, by a paired watch, and by apps with notification access.
The payload is composed **in code** by `push/payload.py` from the escalation row. There is no model call
and no free text from the model.

| `preview_level` | Title | Body | Allowed for |
|---|---|---|---|
| **`none` (default)** | "Ava" | "Needs you" (or "3 things need you") | Everything |
| `sender` | "Ava" | "{sender first name} needs you" | Items whose sender is in the owner's People. Never strangers or unverified senders. |
| `reason` | "Ava" | "{first name}: {reason category}". The category comes from a fixed list (deadline today, wants a call, approval waiting, schedule change). It is not the model's reason text. | Never for `legal_hr`, `payment`, health or "never share" topics: those fall back to `none`. |

- **There are never quotes, amounts, places, meeting names or message text**, at any level. The owner
  opens the app (after unlocking, with a session) to see the item. This tightens ADR 0010 §5 step 0 and
  §9, which allowed "the reason line and the sender's name". **Amendment to 0010**: the lock-screen-safe
  preview on every rung, including Telegram and Slack, follows this table, and the default is `none`.
- The owner chooses the level per device (Settings → Devices) and sees an example of each.
- **Fixed size.** The encrypted payload is padded to a fixed 1 KB (RFC 8291 padding), so its size reveals
  nothing.
- **Format** (one JSON body, valid for both flows):

  ```json
  {
    "web_push": 8030,
    "notification": {
      "title": "Ava",
      "body": "Needs you",
      "navigate": "https://app.example/w/<instance>/needs-you?d=<delivery token>",
      "tag": "esc-<delivery token>",
      "app_badge": 3,
      "silent": false
    },
    "superaide": { "v": 1, "instance": "<instance slug>", "token": "<delivery token>", "kind": "escalation", "actions": ["ack", "snooze30"] }
  }
  ```

  - Safari 18.4+ can show it declaratively, even if the service worker fails.
  - Our service worker reads `superaide` and adds the Android action buttons.
  - The token is the 12-character opaque `escalation_delivery.token` from 0010 §6, never the escalation
    id. It authorises nothing by itself: every action also needs the owner's session (3.4).
- **Headers.**
  - `TTL`: the rung's timeout (default 180 s for rung 0). A push that arrives after the ladder has
    moved on is useless.
  - `Urgency: high` for interrupts, `low` for digests and reminders.
  - `Topic: esc-<token>` so a queued push for the same item is replaced, not stacked.

#### 3.4 Acknowledge and Snooze, hooked into the escalation acks

| Platform | What the owner can do from the notification | What counts as an ack |
|---|---|---|
| Android (Chrome PWA) | Two action buttons: **Acknowledge** and **Snooze 30m**. A tap on the body opens the item. | The Acknowledge button. Snooze moves the escalation to `deferred` (0010 state machine). |
| iOS/iPadOS (Home Screen app) | Tap only (no action buttons in Safari). The item opens with Acknowledge, Reply, Snooze and Not urgent at the top. | A button press **inside the app**. Opening the item doesn't count, because "seen" is not a state (0010 §5). |
| Desktop browsers | Like Android where `actions` is supported | Same |

- **Action route.** The `notificationclick` handler POSTs to `/api/app/push/action` with
  `{token, verb}`.
  - Service-worker fetches are same-origin, so the session cookie goes with them. The worker sets
    `X-Requested-With` itself, and `X-Superaide-Instance` from the payload's `instance` field. Under
    ADR 0009 §2 the scope must be set before any data is read (RLS returns nothing unscoped), and a push
    action has no channel endpoint to derive it from. So `current_instance` runs first, and the token is
    looked up only inside that scope.
  - The API checks that the session's account is the owner of the token's instance (`current_instance`,
    0009 §2). Anyone else gets a 404 and an `escalation_ack_refused` audit row.
  - The API then calls the same `triage/escalate.py` ack function as the Telegram and Slack buttons:
    `escalation_ack(source='push', external_id='<token>:<subscription_id>:<verb>')`. The unique index
    turns a double tap into a no-op. The state moves under the row lock and the next step's job is
    cancelled.
  - If the session has expired, the worker opens the item URL instead. The owner logs in and acks
    there; nothing is acked silently.
- **Stale notifications.** We can't clear a notification on iOS after an ack elsewhere, because a push
  that shows nothing counts toward the three strikes. So a stale tap opens the item, and the item shows
  "Already acknowledged via Telegram at 14:03". On Android the worker also calls `getNotifications({tag})`
  and closes matching notifications whenever the app or worker next runs.
- **Reply** always opens the app; there is no inline reply on the lock screen. Approvals, Reply and
  Approve-draft need an unlocked phone and the app, by design.
- **Delivery is never seen.** A `201` from the push service (or Apple's success for a dead subscription)
  only means the push was sent. The ladder still waits for an explicit ack.

#### 3.5 Quiet hours, budget and health

- **Quiet hours.** The push rung follows 0010 §5 exactly:
  - no push during `quiet_start`–`quiet_end` unless the item is VIP and critical with
    `vip_breaks_quiet`;
  - deferred items join the catch-up when quiet hours end;
  - digests and reminders also wait until quiet hours end.
- **Per-device mute.** `muted_until` ("Mute this phone for 1h, until tomorrow, or always") skips that
  device only. If every device is muted, the rung is skipped and the ladder goes to rung 1 at once.
- **Interrupt budget.** A push counts as the interrupt at the first push of an escalation (0010 §5).
  Digest and reminder pushes don't count.
- **Health.**
  - A `404` or `410` from the push service sets `gone_at` and disables the row.
  - A `413` is a payload bug: alert, and don't retry.
  - A `429` or `5xx` retries with backoff inside the rung's TTL.
  - The rung counts as healthy if at least one enabled, unmuted, not-gone device exists that was seen
    within 30 days. Otherwise it is skipped (0010's `channels/health` rule), and Connections shows "Push
    is off on all your devices."
- **On iOS, Focus can hold web pushes indefinitely** and the PWA can't mark them Time Sensitive. The
  onboarding step that turns on notifications therefore says: "Add Ava to your Focus allowed apps, or
  keep Telegram as your backup." The Telegram/Slack rungs are the safety net. This gap is the first
  native trigger (section 8).
- **Rung order.** Push becomes rung 0, next to the Needs-you inbox. When the owner has a healthy push
  device, rung 1 (Telegram or Slack) starts at **+3 minutes** instead of at t = 0. The 0010 table already
  allows this ("t = 0 if interrupt_channel ≠ app, else +3 min"). `interrupt_channel` gains the value
  `push`.

#### 3.6 Device binding and sign-out

- On the web, `push_subscription.session_id` ties a device's subscription to the session that created it
  (native uses `device_id`, section 5).
  `auth/service.py` `logout`, `revoke_all` and `reset_password` delete those subscriptions in the same
  transaction. This is a small, coordinated edit in `auth/`, which T2/T9 own. After a "log out
  everywhere", no phone keeps receiving Ava's pushes.
- **Ending an instance** (0009) deletes its subscriptions with its other rows.
- **Shared devices.** On logout the app also calls `subscription.unsubscribe()` on the client, so the
  browser's endpoint dies even if the server-side deletion is missed. The server can't do this for us:
  rows are unique per `(agent_id, endpoint_hash)`, and RLS hides another instance's row, so a second
  account signing into the same browser profile can't see or replace the first one's subscription. The
  residual risk is a shared phone where the first owner never signed out. The content-free default
  (3.3) limits what that phone shows, and Settings → Devices lets the owner remove it remotely.

### 4. Other pushes

| Purpose | When | Urgency | Body (level `none`) |
|---|---|---|---|
| Digest ready | Each digest (0010 TR8), outside quiet hours | low | "Your evening update is ready" |
| Reminder due | `intentions` due time, outside quiet hours unless the owner set the reminder for that time | normal | "Reminder" (level `reason`: the reminder's own title, which the owner wrote) |
| Approval waiting | Bundled into the digest, or pushed only when it's part of an escalation | low | "Something's waiting for your approval" |
| Test | Settings → Devices → "Send a test" | high | "Ava can reach this phone" |

These don't count against the interrupt budget, and at most 2 non-escalation pushes are sent a day.

### 5. Auth for native, later

ADR 0003 says no JWTs, so native keeps **opaque tokens**, stored hashed, revocable immediately.

- **Login.** `POST /auth/token` takes `{email, password, device}` and applies the same lockouts and rate
  limits as `/auth/login`. It returns:
  - `access_token`: 32 random bytes, valid for 15 minutes;
  - `refresh_token`: 32 random bytes, valid for 30 days, sliding.

  The client keeps both in the Keychain or Keystore (`expo-secure-store`), never in AsyncStorage.
- **Refresh rotation.** `POST /auth/refresh` is single-use. Each refresh issues a new pair and marks the
  old refresh token used. **Reuse detection:** presenting an already-used refresh token revokes the whole
  `family_id` and writes `auth_event: refresh_reuse`, because one copy was stolen.
- **Device binding.**
  - At first login the app generates a P-256 key pair in the Secure Enclave or Android Keystore (not
    exportable) and sends the public key with the `device` record.
  - Every refresh must carry a signature over a server nonce and the token (proof of possession, in the
    style of RFC 9449 DPoP). A refresh token copied off the device is useless.
  - App Attest and Play Integrity are optional extras for later (open question 7).
- **No CSRF header for bearer requests.** An `Authorization: Bearer` header is never sent automatically
  by a browser, so CSRF doesn't apply.
  - `require_csrf_header` skips the `X-Requested-With` check when the request has a bearer token and no
    session cookie.
  - Cookie requests keep the check.
  - **A request with both is rejected (400)**, so a confused client can't mix them.
- **Tables.**
  - `user_session` gains `kind` (cookie | native), `device_id`, `family_id`, `parent_id`, `used_at`,
    `refresh_expires_at`.
  - New `user_device` (platform-wide, like `user_session` in 0009): `id, user_id, platform, label,
    public_key, created_at, last_seen_at, revoked_at`.
  - Native `push_subscription` rows bind to `user_device.id` (`device_id`), not to `session_id`, because
    the session rows rotate every 15 minutes. Revoking the device or a reused family removes its push
    rows.
- **Settings → Devices** lists browsers and phones together: "Shashank's iPhone, last seen 2 min ago",
  with a Sign out button per device.

### 6. Native push, later

- A `PushProvider` protocol in `push/providers/` (the ADR 0007 pluggable pattern) with three
  implementations: `webpush` (phase 1), `apns` (`.p8` token auth, HTTP/2, `interruption-level:
  time-sensitive` for interrupts) and `fcm` (HTTP v1, `priority: high`, `visibility: private` with a
  content-free public version).
  - The APNs key and the FCM service account are platform credentials in `platform_secret`.
  - The payload rules in 3.3 apply unchanged. Native adds a Notification Service Extension that can fetch
    the item text after the device is unlocked, if the owner chooses `reason`.
  - iOS action categories carry Acknowledge, Snooze and Reply. Reply uses `UNTextInputNotificationAction`
    and needs the device to be unlocked (`.authenticationRequired`).
- **Expo push is for development only.** In production we send to APNs and FCM directly: one sub-processor
  fewer, and Expo's servers would otherwise see payloads. `getDevicePushTokenAsync` gives native tokens.
- Time Sensitive needs the "Time Sensitive Notifications" capability in the app. **Critical alerts are
  not requested**: they need an Apple entitlement, and the phone rung (0010 TR11) covers critical items.

### 7. Voice ("tell Ava")

- **Phase 1 (PWA).** A mic button in the Chat composer and on Needs-you items ("Reply by voice").
  - Use `SpeechRecognition` with `processLocally = true` only when `SpeechRecognition.available()` says
    the owner's language can run on the device. Offer `install()` after a tap.
  - Otherwise, show a hint to use the keyboard's dictation key. The text box works with it unchanged, and
    on current iPhones and Pixels dictation runs on the device for major languages.
  - **We never upload audio to our servers, and we don't fall back to cloud recognition without saying
    so.** The existing `speech.ts` comment ("in Chrome the browser sends the audio to Google") becomes a
    visible one-line notice if the owner opts into cloud recognition. Brave and Firefox get the dictation
    hint.
  - The transcript goes in as ordinary owner text, so the pipeline is unchanged.
  - The Telegram voice-note idea (growth idea 6) is separate and stays as written: owner-only voice notes,
    server-side speech-to-text inside the spend cap.
- **Native.** iOS 26 `SpeechAnalyzer`/`SpeechTranscriber` (on-device, with a system model) and Android
  `createOnDeviceSpeechRecognizer`, plus a share-sheet and Siri "Tell Ava …" intent. No voiceprints,
  following the growth-ideas guidance.

### 8. When to go native (measured triggers)

ADR 0010 already records time to ack per rung (`escalation_ack.at − escalation_delivery.sent_at`). Over
30 days of owners who have the PWA:

1. **Missed on iOS.** On iOS, at least 25% of push-rung escalations end acked on rung 1 or later, or
   expire, where the owner later says Focus held them (a one-tap "Why did you miss this?" with a "my
   phone was on Focus" option). This is the Time Sensitive gap.
2. **Subscription loss.** At least 10% of iOS push subscriptions become `gone` or silently vanish (the
   re-upsert finds none) per month.
3. **Owner demand.** Owners ask for Siri, widgets, a share sheet, the Watch, or "an App Store app" (a
   trust signal) often enough that the owner decides to act.

If none of these fire, we stay on the PWA. If any fires, the native work packages (N1–N4) start, after
the open store questions (IAP, consent screen) are answered.

## Work packages

Paths are relative to `backend/src/superaide/` or `frontend/src/` unless stated. "Now" means it can run
beside the in-flight ADR 0009 and 0010 work without touching their files. Everything ships behind
`PUSH_ENABLED=false` (backend; when it's false or no VAPID keys exist, `/app/push/*` returns 404) and
`NEXT_PUBLIC_PWA=0` (frontend; when it's 0, no manifest link, no service worker registration and no
install hint).

| WP | Owned files | Delivers | Tests | When |
|---|---|---|---|---|
| **M0 PWA shell** | new `app/manifest.ts`, icons in `app/` (192, 512, maskable), new `frontend/public/sw.js`, new `features/pwa/{register,install}.tsx`, `next.config.ts` (headers for `/sw.js` only) | Installable app, SW with push/click handlers, install hint (Android prompt, iOS sheet), re-subscribe on open | Playwright mobile: the manifest is served and valid, the SW registers, no `/api` response appears in Cache Storage, and logout calls `unsubscribe()`. Unit: `display-mode` detection. | **Now**, behind `NEXT_PUBLIC_PWA` |
| **M1 Push schema + VAPID** | new `push/{__init__,models,vapid}.py`, new Alembic revision (chained at merge), one import line in the metadata registry, `cli.py` `push-keys` subcommand (small), `.env.example` (`PUSH_ENABLED`, `VAPID_SUBJECT`) | Tables in 3.1; key generation into `platform_secret` (until 0009 T0 lands, a vault row under a reserved `platform/vapid` slot, moved by T0's migration) | Revision up/down on a seeded copy; every new table has `agent_id` (the RLS meta-test covers it); `secret_enc` moved to another instance fails to decrypt | **Now** |
| **M2 Subscribe API + devices** | new `push/routes.py` (router mounted from `main.py`, one line): `GET /app/push/key`, `PUT /app/push/subscription` (upsert), `DELETE`, `PATCH` (label, preview level, mute), `POST /app/push/test`, `GET /app/push/devices` | Subscribe and unsubscribe, device list, test push | CSRF required; owner-only (B gets 404 on A's rows); an upsert by another user replaces the row; flag off gives 404 | **Now** against `owner_person_id`; switch to `current_instance` when 0009 T2 merges |
| **M3 Sender + payload** | new `push/{payload,send}.py` (`pywebpush` or `webpush`, aes128gcm, TTL, Urgency, Topic, padding), `pyproject.toml` (one dependency) | The privacy table in 3.3; fan-out with `push_send`; handling of 404/410/413/429 | **Payload snapshots** for every preview level × topic class: `legal_hr`/`payment`/never-share fall back to `none`; strangers never show a name; no quote ever; constant padded size. A fake push service (`respx`) for 201/404/410/413/429; a dead subscription becomes `gone`. | **Now** |
| **M4 Ladder rung + acks** | `triage/escalate.py` (register the `push` rung, one entry, **owned by TR3**), `push/routes.py` `POST /app/push/action`, `sw.js` action handling (sends `X-Superaide-Instance`), `interrupt_channel` value `push` (`db/models.py` comment and onboarding option) | Push as rung 0, Acknowledge/Snooze into `escalation_ack`, stale-tap messages, quiet hours and per-device mute | Ack stops the ladder and cancels the next job; a double tap is a no-op; a non-owner session is refused and audited; quiet hours defer; all devices muted skips the rung; Playwright: dispatch a synthetic `PushEvent` inside the Chromium SW, then check `getNotifications()` and the action routing | **After TR3 and TR7**; prepare on a branch now |
| **M5 Sign-out hooks** | `auth/service.py` (`logout`, `revoke_all`, `reset_password`: delete the session's push rows) | No pushes after sign-out | Logout removes the device's rows; "log out everywhere" removes all of them | Small edit, **coordinate with T2/T9** (owners of `auth/`) |
| **M6 Mobile-first screens** | `features/dashboard/Shell.tsx` and `AppSidebar.tsx` (bottom tab bar below `md`), new `features/digest/*` + `app/(site)/dashboard/digest/page.tsx`, new `app/(site)/dashboard/approvals/page.tsx` (reusing `ApprovalsCard`), a `GET /app/digests` read endpoint in `push/routes.py` or `proactive/` (coordinate with T5) | Tab bar with the Needs-you count, digest page, composer pinned above the keyboard | New `e2e/tests/mobile-nav.mobile.spec.ts`: tab bar visible on Pixel 7, hidden on desktop, no sideways scroll; digest page renders the 0010 sections | Tab bar and digest UI **now**; the Needs-you tab links to TR9's page (TR9 owns it); **state** the `Shell.tsx` edit to T12/TR9 before starting |
| **M7 Digest and reminder pushes** | `proactive/digest.py`, `intentions/` due hook (one call each to `push.send`) | Section 4 | Quiet hours; at most 2 a day; not counted in the interrupt budget | **After TR8 / T5** |
| **M8 Voice** | new `features/voice/{recognizer,MicButton}.tsx` (reusing the types in `features/onboarding/speech.ts`), composer slot in `ChatPage.tsx` | On-device recognition where available, dictation-key hint otherwise, no audio upload | Unit: feature detection with `available()` mocked as available, downloadable or unavailable; Playwright: the mic button is hidden when unsupported; a network check that no audio request leaves the page | **Now**, behind `NEXT_PUBLIC_VOICE` |
| **M9 Devices settings** | `features/dashboard/SettingsPage.tsx` (Devices section) | Device list, preview level with examples, mute, test, remove | Component tests; E2E: turn on → test push → remove | After M2 |
| **M10 Real-device checklist** | `e2e/README.md` (a manual section) | Repeatable checks on an iPhone (iOS 26), an iPad, an EU-region iPhone and a Pixel: install, subscribe, receive, tap, ack in the app, Android action buttons, Focus behaviour, a session older than 7 days, lock-screen text at each level, and whether the service worker can replace a declarative notification on iOS (a possible `mutable` flag) | Manual, run before enabling the flags in production; results recorded in the PR | With M4 |
| **N1 Native token auth** (later) | `auth/{tokens,devices}.py`, `auth/routes.py` (`/auth/token`, `/auth/refresh`, `/auth/devices`), `require_csrf_header` bearer branch, Alembic revision | Section 5 | Refresh reuse revokes the family; a signature over the nonce is required; a request with both cookie and bearer is rejected; lockouts shared with `/auth/login` | Only after a section 8 trigger |
| **N2 Shared API client** (later) | new `packages/api-client/` extracted from `lib/api.ts` + `lib/http.ts` (transport and auth injected); `frontend` imports it | One typed client for web and native | Existing frontend tests unchanged; a contract test against the OpenAPI schema | Later; `lib/api.ts` is owned by T12/TR9 |
| **N3 Expo app** (later) | new `mobile/` (Expo SDK, `expo-secure-store`, `expo-notifications`) | Needs you, Chat, Approvals, Reminders, Digest, voice, AI-provider consent screen (5.1.2(i)), report-a-reply (1.2/4.7, Play), in-app account deletion (5.1.1(v), using 0009 T10's delete job) | Maestro or Detox flows on simulators; store pre-review checklist | Later |
| **N4 APNs/FCM providers** (later) | `push/providers/{apns,fcm}.py` | Section 6 | Fake APNs/FCM servers; Time Sensitive set only for interrupts; FCM `visibility` private | With N3 |

**Order:** M0, M1, M3 and M8 start now in parallel, then M2. M6's UI starts now, once the `Shell.tsx`
ownership is agreed. M4 lands after TR3/TR7, M5 with T2/T9, and M7 after TR8/T5. M10 runs before either
flag turns on in production. N1–N4 wait for a section 8 trigger.

## Cost

| Item | Phase 1 (PWA) | Phase 2 (native) |
|---|---|---|
| Store and program fees | $0. Web push needs no Apple Developer Program membership. | Apple Developer Program US$99 a year; Google Play US$25 once |
| Push delivery | $0 (Apple, Google and Mozilla push services are free) | $0 direct to APNs/FCM (Expo push is free too, but not used in production) |
| Model spend | **$0 per push**: payloads are composed in code, with no model call | Same |
| Voice | $0 (on-device or keyboard dictation) | $0 (on-device) |
| Server | A send costs one HTTP POST per device. Two devices per owner and 10 pushes a day is noise. | Same |
| Builds | None | EAS Build free tier or a paid plan (check current Expo pricing), or local Xcode builds on the Mac |
| Engineering | About 3–4 weeks of focused work for M0–M10 | About 6–10 weeks for N1–N4, then ongoing store releases (review per release, OS-version upkeep, two more test targets) |
| Revenue risk | None | Apple/Google commission of 15–30% if subscriptions must be offered as in-app purchases (open question 5) |

## Consequences

- The owner gets an installable Ava on iPhone and Android this phase, with no store dependency, and the
  first rung of the ladder is a push to the phone instead of only an inbox entry.
- The lock screen never carries content by default. This is stricter than ADR 0010 §9 and applies to the
  Telegram and Slack rungs too.
- On iOS there are no action buttons and no Time Sensitive for web push, so iOS acks happen inside the
  app and Focus can delay a push. The Telegram/Slack rungs stay in place as the safety net, and this gap is
  measured as the first native trigger.
- There is one more credential to guard (the VAPID private key, platform-wide), and rotating it costs
  every subscription.
- Amendments recorded here:
  - **ADR 0010**: `escalation_ack.source` gains `push`; one `escalation_delivery` push row fans out
    through `push_send`; previews follow section 3.3; rung 1 starts at +3 min when push is healthy.
  - **ADR 0009 §6**: `platform_secret` holds the VAPID keys (and later the APNs and FCM keys).
  - **ADR 0003**: native bearer tokens skip the CSRF header, and mixed cookie-plus-bearer requests are
    rejected.
- Risk: iOS PWA behaviour changes with each iOS release (the 2024 EU episode). M10's device checklist runs
  on every iOS major release.

## Open questions for the owner

1. **Default lock-screen preview.** Is "Ava / Needs you" with no name the right default, with the
   sender's name and reason category as opt-in per device? Or would you rather have the first name shown
   by default?
2. **Push as the first rung.** When your phone has push on, should Telegram/Slack wait 3 minutes for you
   to ack the push (proposed), or still fire at once?
3. **Focus on iPhone.** Is it acceptable that iOS Focus can hold Ava's pushes (a web app can't break
   through), with Telegram as the backup? Or is breaking through Focus important enough to go native
   sooner?
4. **Native triggers.** Do you accept the three triggers in section 8 (25% of iOS escalations missed
   because of Focus, 10% subscription loss a month, owner demand), or do you already want an App Store
   presence for trust and discovery?
5. **Paying through the App Store.** If we go native: offer the plan as an in-app purchase (Apple/Google
   take 15–30%), or ship a free app with no purchase screens and accept the review risk under 3.1.3?
6. **Voice.** On-device only, with the keyboard's dictation as the fallback (proposed)? Or may the owner
   opt into cloud recognition (Google via Chrome) with a visible notice?
7. **Device attestation for native.** Should native sign-in later require Apple App Attest / Play
   Integrity, or is the Secure-Enclave-bound refresh token enough?
8. **App name on the Home Screen.** "Ava" (what you hired) or "Superaide"?
9. **Reminder pushes.** May a reminder push show the reminder's own title on the lock screen when you've
   chosen the `reason` level, or should reminders also stay content-free?

## Sources

Internal: `.delivery/product-vision.md` ("Phone and mobile"), `docs/decisions/0003`, `0005`, `0007`,
`0009`, `0010`, `docs/research/AGI era growth ideas.md` (ideas 6 and 11, voice notes, the phone rung,
no wearable).

External (fetched or searched 2026-10-22):

- WebKit, Web Push for Web Apps on iOS and iPadOS: https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
- WebKit, Meet Declarative Web Push: https://webkit.org/blog/16535/meet-declarative-web-push/
- WebKit, Safari 18.4 features: https://webkit.org/blog/16574/webkit-features-in-safari-18-4/
- WebKit, Safari 18.5 features: https://webkit.org/?p=16923
- WebKit, Meet Web Push: https://webkit.org/?p=12945
- WebKit commit, silent push quota: https://webkit.googlesource.com/WebKit/+/108ccb442e40e9705d212e74c1f213e3b5a0ea05
- WebKit bug 273063 (subscriptions invalid, no pushsubscriptionchange): https://bugs.webkit.org/show_bug.cgi?id=273063
- WebKit bug 237350 (logged out after 7 days): https://bugs.webkit.org/show_bug.cgi?id=237350
- Progressier, iOS subscriptions terminated after 3 notifications: https://dev.to/progressier/how-to-fix-ios-push-subscriptions-being-terminated-after-3-notifications-39a7
- Apple forums, actions on iOS 16.4: https://developer.apple.com/forums/thread/726793
- Apple forums, success for invalid subscriptions: https://developer.apple.com/forums/thread/719990
- MDN browser-compat-data, Notification: https://github.com/mdn/browser-compat-data/blob/main/api/Notification.json
- heise, iOS 26 Home Screen web apps: https://heise.de/-10749652
- WebKit, News from WWDC25: https://webkit.org/?p=16993
- Search Engine Land, Safari 7-day storage cap: https://searchengineland.com/what-safaris-7-day-cap-on-script-writeable-storage-means-for-pwa-developers-332519
- TechCrunch, EU Home Screen web apps: https://techcrunch.com/?p=2666084
- ComputerBase, Apple keeps PWAs in the EU: https://www.computerbase.de/2024-03/dma-apple-deaktiviert-progressive-web-apps-mit-ios-17-4-doch-nicht/
- Chrome, Badging API: https://developer.chrome.com/docs/capabilities/web-apis/badging-api
- AOSP, notification visibility default: https://gitlab.e.foundation/e/os/android_frameworks_base/-/commit/0bf2ed8ae394b327af1bc6e853a698c2dac67aaa
- OneSignal, iOS Focus modes and interruption levels: https://documentation.onesignal.com/v9.0/docs/ios-focus-modes-and-interruption-levels
- pkgpulse, React Native vs Expo vs Capacitor 2026: https://www.pkgpulse.com/guides/react-native-vs-expo-vs-capacitor-cross-platform-mobile-2026
- Apple forums, Capacitor 4.2 rejection: https://developer.apple.com/forums/thread/812889
- MobiLoud, WebView wrapper guidelines: https://www.mobiloud.com/blog/app-store-review-guidelines-webview-wrapper
- Expo, push notifications FAQ: https://docs.expo.dev/push-notifications/faq/
- pywebpush: https://github.com/web-push-libs/pywebpush ; webpush: https://pypi.org/project/webpush
- Apple, App Store Review Guidelines: https://developer.apple.com/app-store/review/guidelines/
- Coverage of the 5.1.2(i) AI revision: https://www.igeeksblog.com/apple-app-store-ai-data-disclosure-guidelines
- Google Play, AI-Generated Content policy: https://support.google.com/googleplay/android-developer/answer/14094294
- MDN, SpeechRecognition.available(): https://developer.mozilla.org/docs/Web/API/SpeechRecognition/available_static
- Microsoft Edge, speech recognition API: https://learn.microsoft.com/fi-fi/microsoft-edge/web-platform/speech-recognition-api
- Apple WWDC25 session 277, SpeechAnalyzer: https://developer.apple.com/videos/play/wwdc2025/277/
- addpipe, SpeechAnalyzer overview: https://blog.addpipe.com/apple-speechanalyzer-api/

Unverified, to check on a device (M10): action buttons on current iOS (BCD says none), any interruption
level for web push, push in EU-region Home Screen apps, and Chrome's `processLocally` rollout and
languages.
