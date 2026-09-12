# Local forecast HTTP boundary

GET requests use the forecast-specific validated reader in
`src/lib/weather-forecast/server.ts`. It is disabled in production, accepts only
a configured loopback Python service, rejects unknown query fields and preserves
typed gap/refusal states. Errors never trigger a different data source. Agent
parity is the exported tool calling this same reader with the same input schema.
