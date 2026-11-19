"""Manual failover demo: send a payment every 0.5 s and print where each one went.

Terminal 1:   docker compose run --rm app paystream
Terminal 2:   docker kill bank-primary      (then later)   docker start bank-primary

Ctrl+C prints a summary.
"""

import time
from datetime import datetime

import payment_app as app


def log(message):
    print(f"{datetime.now():%H:%M:%S}  {message}", flush=True)


app.setup()
log("payment stream started - crash a bank server in another terminal (docker kill bank-primary)")
log(f"[observer] {app.one_line(app.replica_status())}")
results, last_state, number = [], None, 0
try:
    while True:
        number += 1
        payment = app.send_payment(number, budget=30.0)
        results.append(payment)
        state = app.one_line(app.replica_status())
        if state != last_state and last_state is not None:
            log(f"[observer] {state}")
        last_state = state
        retries = f" after {payment.attempts} attempts, {payment.seconds:.1f} s  [{app.summarize_errors(payment.errors)}]" \
            if payment.attempts > 1 else ""
        where = f" on {app.short(payment.server)}" if payment.server else ""
        log(f"payment #{number:03d} {payment.outcome}{where}{retries}")
        time.sleep(0.5)
except KeyboardInterrupt:
    pass

acknowledged = sum(r.outcome in ("committed", "already paid") for r in results)
unknown = sum(r.outcome == "unknown" for r in results)
total = sum(a["balance"] for a in app.accounts.find())
print(f"\n{len(results)} payments sent: {acknowledged} acknowledged, "
      f"{len(results) - acknowledged - unknown} refused, {unknown} with an unknown result; "
      f"history holds {app.transfers.count_documents({})}; total money {total:,}")
