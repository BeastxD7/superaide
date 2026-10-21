import { redirect } from 'next/navigation'

// The workspace became the dashboard; old links land in its chat.
export default function WorkspacePage() {
  redirect('/dashboard/chat')
}
