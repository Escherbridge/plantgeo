# Environmental contracts

This directory contains browser-safe value tables and data contracts shared by
map components and server adapters. Keep network clients, credentials, database
access, and provider-specific parsing under `src/lib/server/`; browser code must
depend only on these inert contracts.

Types describe evidence returned by first-party APIs. They do not imply that a
provider is configured or that a validated warehouse release is available.
