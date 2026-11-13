import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Batch, type Buyer, type Grower, type Order } from '../../lib/api'

export type OverviewData = { growers: Grower[]; batches: Batch[]; buyers: Buyer[]; orders: Order[] }
export type OverviewResource = keyof OverviewData
export type OverviewErrors = Partial<Record<OverviewResource, string>>
export type OverviewLoading = Record<OverviewResource, boolean>

const emptyData: OverviewData = { growers: [], batches: [], buyers: [], orders: [] }
const initialLoading: OverviewLoading = { growers: true, batches: true, buyers: true, orders: true }
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : 'Request failed.'

export function useOverviewData() {
  const [data, setData] = useState<OverviewData>(emptyData)
  const [errors, setErrors] = useState<OverviewErrors>({})
  const [loadingByResource, setLoadingByResource] = useState<OverviewLoading>(initialLoading)
  const requestVersion = useRef(0)

  const reload = useCallback(async () => {
    const version = ++requestVersion.current
    setData(emptyData)
    setErrors({})
    setLoadingByResource(initialLoading)

    const loadResource = <K extends OverviewResource>(resource: K, request: Promise<OverviewData[K]>) => request
      .then(value => {
        if (version === requestVersion.current) setData(current => ({ ...current, [resource]: value }))
      })
      .catch(reason => {
        if (version === requestVersion.current) setErrors(current => ({ ...current, [resource]: errorMessage(reason) }))
      })
      .finally(() => {
        if (version === requestVersion.current) setLoadingByResource(current => ({ ...current, [resource]: false }))
      })

    await Promise.all([
      loadResource('growers', api.growers.list()),
      loadResource('batches', api.batches.list()),
      loadResource('buyers', api.buyers.list()),
      loadResource('orders', api.orders.list()),
    ])
  }, [])

  useEffect(() => {
    void reload()
    return () => { requestVersion.current += 1 }
  }, [reload])

  const loading = Object.values(loadingByResource).some(Boolean)
  return { data, errors, loading, loadingByResource, reload, allUnavailable: Object.keys(errors).length === 4 }
}
