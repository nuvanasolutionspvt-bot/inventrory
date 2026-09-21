(() => {
  "use strict";
  let printed = false;
  async function printInvoice() {
    if (printed) return;
    printed = true;
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const url = new URL(window.location.href);
    url.searchParams.delete("print");
    window.history.replaceState(window.history.state, "", url.toString());
    window.print();
  }
  if (document.readyState === "complete") printInvoice();
  else window.addEventListener("load", printInvoice, {once: true});
})();
