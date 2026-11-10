export function LoadingState({ label = 'Loading records…' }: { label?: string }) { return <div className="state"><span className="loading-spinner" aria-hidden="true" />{label}</div> }
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) { return <div className="state error" role="alert"><p>{message}</p>{onRetry && <button className="retry-button" type="button" onClick={onRetry}>Retry</button>}</div> }
export function EmptyState({ message }: { message: string }) { return <div className="state">{message}</div> }
