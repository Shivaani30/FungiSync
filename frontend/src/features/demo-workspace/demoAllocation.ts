import type { DemoBatch, DemoOrder } from './demoData'

export interface DemoCandidate {
  readonly batchId: string
  readonly reason: string
}

export interface DemoAllocationLine {
  readonly batchId: string
  readonly quantityKg: string
}

export interface DemoAllocationPreview {
  readonly candidates: readonly DemoCandidate[]
  readonly excluded: readonly DemoCandidate[]
  readonly lines: readonly DemoAllocationLine[]
  readonly allocatedKg: string
  readonly unfulfilledKg: string
}

const SCALE = BigInt('1000')

function parseMilliKg(value: string): bigint {
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,3}))?$/.exec(value)
  if (!match) throw new Error(`Invalid demo quantity: ${value}`)
  const whole = BigInt(match[1])
  const fraction = BigInt((match[2] ?? '').padEnd(3, '0') || '0')
  return whole * SCALE + fraction
}

function formatMilliKg(value: bigint): string {
  const whole = value / SCALE
  const fraction = String(value % SCALE).padStart(3, '0')
  return `${whole}.${fraction}`
}

/**
 * A transparent, demo-only FIFO illustration. Each order is calculated
 * independently; it does not consume supply across orders or model reservations.
 */
export function buildDemoAllocationPreview(order: DemoOrder, batches: readonly DemoBatch[]): DemoAllocationPreview {
  const candidates: DemoCandidate[] = []
  const excluded: DemoCandidate[] = []
  const matching: DemoBatch[] = []

  for (const batch of batches) {
    if (batch.species.trim().toLocaleLowerCase() !== order.species.trim().toLocaleLowerCase()) {
      excluded.push({ batchId: batch.id, reason: 'Different species in this demo fixture.' })
    } else if (batch.status !== 'confirmed' || batch.confirmedKg === null || batch.demoAvailableKg === null) {
      excluded.push({ batchId: batch.id, reason: 'Harvest quantity is expected, not confirmed in the demo fixture.' })
    } else if (parseMilliKg(batch.demoAvailableKg) <= BigInt('0')) {
      excluded.push({ batchId: batch.id, reason: 'No illustrative demo quantity is listed.' })
    } else {
      matching.push(batch)
      candidates.push({ batchId: batch.id, reason: 'Same species with a confirmed illustrative demo quantity.' })
    }
  }

  matching.sort((left, right) => left.harvestDate < right.harvestDate ? -1 : left.harvestDate > right.harvestDate ? 1 : left.id < right.id ? -1 : left.id > right.id ? 1 : 0)
  let remaining = parseMilliKg(order.quantityKg)
  let allocated = BigInt('0')
  const lines: DemoAllocationLine[] = []

  for (const batch of matching) {
    if (remaining === BigInt('0')) break
    const available = parseMilliKg(batch.demoAvailableKg as string)
    const quantity = available < remaining ? available : remaining
    if (quantity > BigInt('0')) {
      lines.push({ batchId: batch.id, quantityKg: formatMilliKg(quantity) })
      allocated += quantity
      remaining -= quantity
    }
  }

  return {
    candidates,
    excluded,
    lines,
    allocatedKg: formatMilliKg(allocated),
    unfulfilledKg: formatMilliKg(remaining),
  }
}
