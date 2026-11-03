import { LiveTimeline } from '@/components/timeline/LiveTimeline'
import { PageHeader } from '@/components/common/PageHeader'

export function Timeline() {
  return (
    <div>
      <PageHeader
        eyebrow="Overview"
        title="Event timeline"
        sub="Every backend event as it happened — the system narrating itself."
      />
      <LiveTimeline limit={400} />
    </div>
  )
}
