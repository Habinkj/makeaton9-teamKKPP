# Brightloop Subscription Management System

Odoo x Make-A-Ton 9.0 (virtual round). Team KKPP.

## State machines

**Subscription**
| From | Allowed next |
|---|---|
| draft | quotation, closed |
| quotation | confirmed, draft, closed |
| confirmed | active, closed |
| active | closed |
| closed | none (renewal creates a new subscription) |

**Invoice**
| From | Allowed next |
|---|---|
| draft | confirmed, cancelled |
| confirmed | paid, cancelled |
| paid | none |
| cancelled | none |

## Assumptions
- Money is stored as integer paise and tax rates as basis points; no floating point.
- Proration, dunning and grace periods are not implemented.
- Plan minimum quantity is the total quantity across subscription lines.
- Customers can close early only if the plan allows it; staff can always close.
- A renewal is a new subscription at current prices.
- There is no Paused state; pausing is a flag on an active subscription.
