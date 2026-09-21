# Tenant Razorpay payments

1. Enable Online Payment for the restaurant in Company > Businesses > Edit (or Tenant Features in Django admin).
2. Sign in as Restaurant Admin and open Restaurant > Online Payment.
3. Enter that restaurant's matching Razorpay Key ID and Key Secret, select Enable Razorpay checkout, and click Validate & Save. Begin with Test keys.
4. In POS, choose Pay with Razorpay. The bill stays open until capture is verified by the server.
5. Complete checkout. If it closes or verification loses connectivity, open the bill from Online Payment and click Check payment status before retrying.
6. Complete a Razorpay Test-mode payment and verify the invoice, transaction history and stock deduction before switching to Live keys.

Secrets are encrypted using PAYMENT_CREDENTIAL_KEY in the deployment environment. This workspace has a generated key in its ignored .env file. Preserve and back up that key separately from database backups; losing or replacing it makes saved secrets unreadable. Never commit .env or send keys in chat. Other deployments must install requirements.txt and supply their own secure encryption key (or the original key when restoring this database). Restart existing server processes after configuration changes.

Blank Secret keeps the saved value. A new Key ID requires a new Secret. Keys are checked against Razorpay before saving. Merchant-key changes are blocked while payment orders remain pending. Gateway orders freeze the associated bill amount; retry the same checkout or reconcile its payment status rather than editing or manually collecting that bill again.

The integration accepts INR payments for unpaid open restaurant bills of at least INR 1.00. Existing manual cash/card/UPI billing remains available for bills without gateway orders. Payment capture updates the existing bill, table status and ingredient consumption in one local transaction. Gateway details are kept separately; the existing Bill/Sale schema is unchanged. Platform subscription Razorpay credentials remain separate from merchant credentials.

Browser callback signatures are verified using the stored order ID; payment ID, order ID, amount, currency and capture status are checked with Razorpay. Authenticated Check payment status recovers lost callbacks. No webhook or automatic background reconciliation is configured; an interrupted checkout requires this status check. Refunds and cancellation/voiding gateway orders are not implemented in this module.

Automated tests simulate Razorpay responses, use a separate test database and never charge cards. Actual merchant Test/Live transactions require tenant credentials and Razorpay account activation. SQLite does not exercise MySQL row-lock contention.

Provider references:
- https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/
- https://razorpay.com/docs/api/payments/capture/
- https://razorpay.com/docs/api/orders/fetch-payments/
- https://cryptography.io/en/stable/fernet/
