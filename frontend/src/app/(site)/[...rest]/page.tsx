import { notFound } from 'next/navigation'

// Any other address inside the site shows the site's own "not found" page.
export default function CatchAll() {
  notFound()
}
