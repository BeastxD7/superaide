import { redirect } from 'next/navigation'

// The page was renamed Capabilities; old links and bookmarks still land there.
export default function Page() {
  redirect('/dashboard/capabilities')
}
