import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Batch, type Buyer, type Grower, type Order } from '../../lib/api'

export type OverviewData = { growers: Grower[]; batches: Batch[]; buyers: Buyer[]; orders: Order[] }
export type OverviewResource = keyof OverviewData
export type OverviewErrors = Partial<Record<OverviewResource, string>>

const emptyData: OverviewData = { growers: [], batches: [], buyers: [], orders: [] }
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : 'Request failed.'

export function useOverviewData() {
  const [data, setData] = useState<OverviewData>(emptyData)
  const [errors, setErrors] = useState<OverviewErrors>({})
  const [loading, setLoading] = useState(true)
  const requestVersion = useRef(0)

  const reload = useCallback(async () => {
    const version = ++requestVersion.current
    setLoading(true)
    const results = await Promise.allSettled([api.growers.list(), api.batches.list(), api.buyers.list(), api.orders.list()])
    if (version !== requestVersion.current) return

    const resources: OverviewResource[] = ['growers', 'batches', 'buyers', 'orders']
    const nextData: OverviewData = { ...emptyData }
    const nextErrors: OverviewErrors = {}
    results.forEach((result, index) => {
      const resource = resources[index]
      if (result.status === 'fulfilled') {
        if (resource === 'growers') nextData.growers = result.value as Grower[]
        if (resource === 'batches') nextData.batches = result.value as Batch[]
        if (resource === 'buyers') nextData.buyers = result.value as Buyer[]
        if (resource === 'orders') nextData.orders = result.value as Order[]
      } else nextErrors[resource] = errorMessage(result.reason)
    })
    setData(nextData)
    setErrors(nextErrors)
    setLoading(false)
  }, [])

  useEffect(() => {
    void reload()
    return () => { requestVersion.current += 1 }
  }, [reload])

  return { data, errors, loading, reload, allUnavailable: Object.keys(errors).length === 4 }
}
