import { Skeleton } from '@/components/ui/skeleton'

// Shown inside the dashboard while a screen is on its way.
export default function DashboardLoading() {
  return (
    <div className="flex-1 px-4 py-6 sm:px-6 lg:py-8" aria-busy="true" aria-label="Loading">
      <div className="mx-auto w-full max-w-4xl space-y-4">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  )
}
