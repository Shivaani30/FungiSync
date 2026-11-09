export type BatchStatus = 'expected' | 'confirmed' | 'harvested' | 'cancelled'
export type OrderStatus = 'pending' | 'confirmed' | 'fulfilled' | 'cancelled'
export interface Grower { id:string; name:string; phone:string; location:string; created_at:string; updated_at:string }
export interface Buyer { id:string; name:string; location:string; contact:string; created_at:string; updated_at:string }
export interface Batch { id:string; grower_id:string; species:string; expected_kg:string; confirmed_kg:string|null; harvest_date:string; asking_price_per_kg:string|null; quality_grade:string|null; best_before_date:string|null; status:BatchStatus; created_at:string; updated_at:string }
export interface Order { id:string; buyer_id:string; species:string; quantity_kg:string; price_limit:string; delivery_date:string; required_quality_grade:string|null; status:OrderStatus; created_at:string; updated_at:string }
export interface MatchCriterion { status:string; weight:number; points:string; reason:string }
export interface MatchCandidate { batch_id:string; grower_id:string; order_id:string|null; buyer_id:string|null; eligible:boolean; compatibility_score:string; data_completeness_percentage:string; requested_quantity_kg:string; available_quantity_kg:string|null; quantity_that_could_be_supplied_kg:string; remaining_quantity_kg:string; criteria:Record<string, MatchCriterion>; positive_reasons:string[]; unknown_criteria:string[]; warnings:string[]; exclusion_reasons:string[]; rank:number|null }
export interface MatchResults { target_id:string; total_candidates:number; candidates:MatchCandidate[] }
const base = import.meta.env.VITE_API_BASE_URL
if (!base) throw new Error('VITE_API_BASE_URL is required.')
async function request<T>(path:string, options:RequestInit={}):Promise<T> {
 const response=await fetch(base+path,{...options,headers:{'Content-Type':'application/json',...options.headers}})
 if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(body?.detail||'Request failed ('+response.status+')')}
 return response.status===204?undefined as T:response.json()
}
function resource<T>(path:string){return{list:(params:Record<string,string>={})=>request<T[]>(path+'?'+new URLSearchParams(params)),create:(data:Partial<T>)=>request<T>(path,{method:'POST',body:JSON.stringify(data)}),update:(id:string,data:Partial<T>)=>request<T>(path+'/'+id,{method:'PATCH',body:JSON.stringify(data)}),remove:(id:string)=>request<void>(path+'/'+id,{method:'DELETE'})}}
export const api={health:()=>request<{status:string;database:string}>('/health'),growers:resource<Grower>('/growers'),buyers:resource<Buyer>('/buyers'),batches:resource<Batch>('/batches'),orders:resource<Order>('/orders')}
export const matching = {
 forOrder:(orderId:string, params:Record<string,string>={}) => request<MatchResults>('/matches/orders/'+orderId+'?'+new URLSearchParams(params)),
 forBatch:(batchId:string, params:Record<string,string>={}) => request<MatchResults>('/matches/batches/'+batchId+'?'+new URLSearchParams(params)),
}
