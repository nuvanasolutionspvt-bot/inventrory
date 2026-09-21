(() => {
  "use strict";
  const urls = JSON.parse(document.getElementById("razorpay-urls").textContent);
  const csrf = document.querySelector("#razorpay-token input").value;
  const pay = document.getElementById("razorpay-pay");
  const check = document.getElementById("razorpay-check");
  const message = document.getElementById("payment-message");
  let busy = false;
  function show(text) { message.hidden = false; message.textContent = text; }
  async function post(url, fields = {}) {
    const response = await fetch(url, {method: "POST", credentials: "same-origin", headers: {"X-CSRFToken": csrf}, body: new URLSearchParams(fields)});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("Could not verify payment. Sign in again if needed and use Check payment status."); }
    if (!response.ok || data.error) throw new Error(data.error || "Payment request failed.");
    if (data.redirect) window.location.assign(data.redirect);
    return data;
  }
  check.addEventListener("click", async () => {
    if (busy) return;
    busy = true; check.disabled = true;
    try { show("Checking Razorpay..."); await post(urls.check); }
    catch (error) { show(error.message); }
    finally { busy = false; check.disabled = false; }
  });
  pay.addEventListener("click", async () => {
    if (busy) return;
    busy = true; pay.disabled = true;
    try {
      if (!window.Razorpay) throw new Error("Razorpay checkout could not load. Check your connection and reload this page.");
      const options = await post(urls.create);
      if (options.redirect) return;
      const checkout = new window.Razorpay({...options,
        handler: async result => {
          try { show("Verifying payment..."); await post(urls.verify, result); }
          catch (error) { show(error.message + " Use Check payment status to recover."); }
          finally { busy = false; pay.disabled = false; }
        },
        modal: {ondismiss: () => { busy = false; pay.disabled = false; show("Checkout closed. Check payment status before retrying."); }}
      });
      checkout.on("payment.failed", result => {
        const reason = result.error && result.error.description;
        show(reason ? `Payment failed: ${reason}. The bill remains unpaid.` : "Payment failed. Check payment status or review the Razorpay dashboard.");
      });
      checkout.open();
    } catch (error) { busy = false; pay.disabled = false; show(error.message); }
  });
})();
