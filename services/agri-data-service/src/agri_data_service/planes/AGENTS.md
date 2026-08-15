# Layer L3: Planes

## Responsibility
Domain execution planes that bind method algorithms and pipeline acquisition outputs into warehouse persistence.

## Dependency Rules
- **May import**: `foundation` (L0), `method` (L1), `warehouse` (L1), `pipeline` (L2).
- **May NOT import**: `interface` (L4).
