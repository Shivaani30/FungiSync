/** Fixed, fictional records used only by the isolated Demo Workspace. */
export interface DemoGrower {
  readonly id: string
  readonly name: string
  readonly location: string
}

export interface DemoBatch {
  readonly id: string
  readonly growerId: string
  readonly species: string
  readonly expectedKg: string
  readonly confirmedKg: string | null
  readonly demoAvailableKg: string | null
  readonly harvestDate: string
  readonly status: 'confirmed' | 'expected'
}

export interface DemoBuyer {
  readonly id: string
  readonly name: string
  readonly location: string
}

export interface DemoOrder {
  readonly id: string
  readonly buyerId: string
  readonly species: string
  readonly quantityKg: string
  readonly deliveryWindow: string
  readonly status: 'pending' | 'confirmed'
}

export const demoGrowers: readonly DemoGrower[] = [
  { id: 'demo-grower-01', name: 'Samba Demo Grower 01', location: 'Samba' },
  { id: 'demo-grower-02', name: 'Vijaypur Demo Grower 02', location: 'Vijaypur, Samba' },
  { id: 'demo-grower-03', name: 'Jammu Demo Grower 03', location: 'Jammu region' },
]

export const demoBatches: readonly DemoBatch[] = [
  { id: 'demo-batch-01', growerId: 'demo-grower-01', species: 'Oyster mushroom', expectedKg: '32.000', confirmedKg: '26.000', demoAvailableKg: '26.000', harvestDate: '2026-11-16', status: 'confirmed' },
  { id: 'demo-batch-02', growerId: 'demo-grower-02', species: 'Oyster mushroom', expectedKg: '18.500', confirmedKg: '14.250', demoAvailableKg: '14.250', harvestDate: '2026-11-18', status: 'confirmed' },
  { id: 'demo-batch-03', growerId: 'demo-grower-03', species: 'Shiitake mushroom', expectedKg: '20.000', confirmedKg: '12.000', demoAvailableKg: '12.000', harvestDate: '2026-11-20', status: 'confirmed' },
  { id: 'demo-batch-04', growerId: 'demo-grower-03', species: 'Oyster mushroom', expectedKg: '10.000', confirmedKg: null, demoAvailableKg: null, harvestDate: '2026-11-23', status: 'expected' },
]

export const demoBuyers: readonly DemoBuyer[] = [
  { id: 'demo-buyer-01', name: 'Jammu Demo Buyer A', location: 'Jammu' },
  { id: 'demo-buyer-02', name: 'Samba Demo Buyer B', location: 'Samba' },
  { id: 'demo-buyer-03', name: 'Jammu Demo Buyer C', location: 'Jammu region' },
]

export const demoOrders: readonly DemoOrder[] = [
  { id: 'demo-order-01', buyerId: 'demo-buyer-01', species: 'Oyster mushroom', quantityKg: '30.000', deliveryWindow: '2026-11-17', status: 'pending' },
  { id: 'demo-order-02', buyerId: 'demo-buyer-02', species: 'Shiitake mushroom', quantityKg: '16.500', deliveryWindow: '2026-11-21', status: 'pending' },
  { id: 'demo-order-03', buyerId: 'demo-buyer-03', species: 'Oyster mushroom', quantityKg: '45.000', deliveryWindow: '2026-11-24', status: 'confirmed' },
]

const quantityPattern = /^(?:0|[1-9]\d*)(?:\.\d{1,3})?$/
const quantityMilli = (value: string) => {
  const [whole, fraction = ''] = value.split('.')
  return BigInt(whole) * BigInt('1000') + BigInt(fraction.padEnd(3, '0') || '0')
}

/** Fail closed if fixture relationships or exact kg representations drift. */
export function validateDemoDataset(): void {
  const growerIds = new Set(demoGrowers.map(grower => grower.id))
  const buyerIds = new Set(demoBuyers.map(buyer => buyer.id))
  for (const batch of demoBatches) {
    if (!growerIds.has(batch.growerId)) throw new Error(`Demo batch ${batch.id} has no synthetic grower.`)
    if (!quantityPattern.test(batch.expectedKg)) throw new Error(`Demo batch ${batch.id} has an invalid expected quantity.`)
    if (batch.confirmedKg !== null && !quantityPattern.test(batch.confirmedKg)) throw new Error(`Demo batch ${batch.id} has an invalid confirmed quantity.`)
    if (batch.demoAvailableKg !== null && !quantityPattern.test(batch.demoAvailableKg)) throw new Error(`Demo batch ${batch.id} has an invalid demo quantity.`)
    if ((batch.confirmedKg === null) !== (batch.demoAvailableKg === null)) throw new Error(`Demo batch ${batch.id} must distinguish confirmed and demo quantities together.`)
    if (batch.confirmedKg !== null && quantityMilli(batch.confirmedKg) > quantityMilli(batch.expectedKg)) throw new Error(`Demo batch ${batch.id} has more confirmed than expected.`)
    if (batch.confirmedKg !== null && batch.demoAvailableKg !== null && quantityMilli(batch.demoAvailableKg) > quantityMilli(batch.confirmedKg)) throw new Error(`Demo batch ${batch.id} has more demo quantity than confirmed quantity.`)
  }
  for (const order of demoOrders) {
    if (!buyerIds.has(order.buyerId)) throw new Error(`Demo order ${order.id} has no synthetic buyer.`)
    if (!quantityPattern.test(order.quantityKg)) throw new Error(`Demo order ${order.id} has an invalid quantity.`)
    if (quantityMilli(order.quantityKg) <= BigInt('0')) throw new Error(`Demo order ${order.id} must request a positive quantity.`)
  }
}

validateDemoDataset()
