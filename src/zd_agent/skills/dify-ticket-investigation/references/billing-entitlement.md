# Billing, entitlement, quota, and refund

Investigate these as separate facts:

1. resolved workspace identity;
2. subscription and renewal/cancellation state;
3. current entitlement or credit pools;
4. UI display versus actual request consumption;
5. the exact recovery or approval event.

Do not treat a form plan tag as subscription evidence. Do not equate `submitted`
with refunded, canceled, or completed. A remaining/free credit bucket does not by
itself prove downgrade, and a full quota does not explain an unrelated runtime
failure without a matching rejection at the execution boundary.
