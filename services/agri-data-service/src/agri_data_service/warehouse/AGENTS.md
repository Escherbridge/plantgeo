# Layer L1: Warehouse

## Responsibility
Database connection management, session factories, declarative SQL object loading, ORM mappings (`db/` and `models/`).

## Dependency Rules
- **May import**: `foundation` (L0).
- **May NOT import**: `method`, `pipeline`, `planes`, `interface`.
