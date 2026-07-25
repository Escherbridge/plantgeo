# Map interaction boundary

Location selection is a privacy boundary. `AgentInteraction` requires an explicit user choice before analysis begins and defaults to an approximate (two-decimal) location; exact coordinates are opt-in. Regional analysis remains informational only: it cannot take external actions, and unavailable data must remain visibly unavailable rather than producing substitute recommendations.

The action-network layer owns viewport cancellation through `useActionNetworkFeatures`. It may display only the bounded, worker-processed response and provides a retry affordance only for retryable failures. Freshness/revision labels are metadata, not a claim that a forecast or recommendation exists.
