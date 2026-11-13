export type BatchStatus = 'expected' | 'confirmed' | 'harvested' | 'cancelled'
export type OrderStatus = 'pending' | 'confirmed' | 'fulfilled' | 'cancelled'
export interface Grower { id:string; name:string; phone:string; location:string; created_at:string; updated_at:string }
export interface Buyer { id:string; name:string; location:string; contact:string; created_at:string; updated_at:string }
export interface Batch { id:string; grower_id:string; species:string; expected_kg:string; confirmed_kg:string|null; harvest_date:string; asking_price_per_kg:string|null; quality_grade:string|null; best_before_date:string|null; status:BatchStatus; created_at:string; updated_at:string }
export interface Order { id:string; buyer_id:string; species:string; quantity_kg:string; price_limit:string; delivery_date:string; required_quality_grade:string|null; status:OrderStatus; created_at:string; updated_at:string }
export interface MatchCriterion { status:string; weight:number; points:string; reason:string }
export interface MatchCandidate { batch_id:string; grower_id:string; order_id:string|null; buyer_id:string|null; eligible:boolean; compatibility_score:string; data_completeness_percentage:string; requested_quantity_kg:string; available_quantity_kg:string|null; quantity_that_could_be_supplied_kg:string; remaining_quantity_kg:string; criteria:Record<string, MatchCriterion>; positive_reasons:string[]; unknown_criteria:string[]; warnings:string[]; exclusion_reasons:string[]; rank:number|null }
export interface MatchResults { target_id:string; total_candidates:number; candidates:MatchCandidate[] }
export interface SimulatedCancellation { buyer_label:string; quantity_kg:string; event_type:'simulated_order_cancellation'; actual_order_mutated:false; reservation_state_changed:false }
export interface PreservedCommitment { buyer_label:string; quantity_kg:string; unchanged_input_fact:true }
export interface AlternateBuyerProposal { buyer_label:string; quantity_kg:string; status:'PROPOSED' }
export interface HarvestReplanDemo { scenario_id:string; is_synthetic:true; source_supply_kg:string; simulated_cancellation:SimulatedCancellation; preserved_commitments:PreservedCommitment[]; alternate_proposal:AlternateBuyerProposal; unassigned_kg:string; unassigned_basis:'synthetic_reconciliation_remainder'; reconciled_total_kg:string; is_reconciled:true; approval_status:'PENDING'; reservation_committed:false; buyer_commitment_created:false }
export interface AllocationPreviewExclusion { code:string; message:string }
export interface AllocationPreviewLine { batch_id:string; batch_confirmed_kg:string|null; active_reserved_kg:string; remaining_before_preview_kg:string|null; proposed_quantity_kg:string; remaining_after_preview_kg:string|null }
export interface AllocationPreviewExcludedBatch { batch_id:string; batch_confirmed_kg:string|null; active_reserved_kg:string; remaining_before_preview_kg:string|null; reasons:AllocationPreviewExclusion[] }
export interface AllocationPreview {
 order_id:string; is_advisory:true; is_synthetic:false; persisted:false; reservation_committed:false; buyer_commitment_created:false;
 quantity_basis:'confirmed_batch_and_order_quantities_less_active_reservations'; reservation_accounting_applied:true; effective_at:string;
 total_requested_kg:string; active_reserved_order_kg:string; remaining_order_kg:string; total_allocated_kg:string; total_unfulfilled_kg:string;
 allocation_lines:AllocationPreviewLine[]; excluded_batches:AllocationPreviewExcludedBatch[]; order_exclusions:AllocationPreviewExclusion[]; warnings:string[]; is_reconciled:true
}
const base = import.meta.env.VITE_API_BASE_URL
if (!base) throw new Error('VITE_API_BASE_URL is required.')
function validationIssueMessage(issue:unknown):string|undefined {
 if(typeof issue!=='object'||issue===null||Array.isArray(issue))return undefined
 const item=issue as Record<string,unknown>
 if(typeof item.msg!=='string'||!item.msg.trim())return undefined
 const location=Array.isArray(item.loc)?item.loc.filter((part):part is string|number=>typeof part==='string'||typeof part==='number').map(String).join('.'):''
 return location?location+': '+item.msg:item.msg
}
function responseErrorMessage(body:unknown,status:number):string {
 const fallback='Request failed ('+status+')'
 if(typeof body!=='object'||body===null||Array.isArray(body))return fallback
 const detail=(body as Record<string,unknown>).detail
 if(typeof detail==='string'&&detail.trim())return detail
 if(Array.isArray(detail)){
  const messages=detail.map(validationIssueMessage).filter((message):message is string=>Boolean(message))
  if(!messages.length)return 'Request validation failed.'
  const concise=messages.slice(0,3).join('; ')
  return 'Validation failed: '+concise+(messages.length>3?' (and '+(messages.length-3)+' more)':'')
 }
 return fallback
}
async function request<T>(path:string, options:RequestInit={}):Promise<T> {
 const response=await fetch(base+path,{...options,headers:{'Content-Type':'application/json',...options.headers}})
 if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(responseErrorMessage(body,response.status))}
 return response.status===204?undefined as T:response.json()
}
function resource<T>(path:string){return{list:(params:Record<string,string>={})=>request<T[]>(path+'?'+new URLSearchParams(params)),create:(data:Partial<T>)=>request<T>(path,{method:'POST',body:JSON.stringify(data)}),update:(id:string,data:Partial<T>)=>request<T>(path+'/'+id,{method:'PATCH',body:JSON.stringify(data)}),remove:(id:string)=>request<void>(path+'/'+id,{method:'DELETE'})}}
export const api={health:()=>request<{status:string;database:string}>('/health'),growers:resource<Grower>('/growers'),buyers:resource<Buyer>('/buyers'),batches:resource<Batch>('/batches'),orders:resource<Order>('/orders')}
export const matching = {
 forOrder:(orderId:string, params:Record<string,string>={}) => request<MatchResults>('/matches/orders/'+orderId+'?'+new URLSearchParams(params)),
 forBatch:(batchId:string, params:Record<string,string>={}) => request<MatchResults>('/matches/batches/'+batchId+'?'+new URLSearchParams(params)),
}
export const harvestCoordination = {
 demoReplan:() => request<HarvestReplanDemo>('/harvest-coordination/demo/replan'),
}
export const allocationPreview = {
 forOrder:(orderId:string) => request<AllocationPreview>('/allocation-preview/orders/'+encodeURIComponent(orderId)),
}
