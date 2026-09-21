const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function checkoutTest(fail) {
  const elements = {};
  for (const id of ['razorpay-pay', 'razorpay-check', 'payment-message']) {
    elements[id] = {disabled: false, hidden: true, handlers: {},
      addEventListener(event, handler) { this.handlers[event] = handler; }};
  }
  elements['razorpay-urls'] = {textContent: JSON.stringify({create: '/order/', verify: '/verify/', check: '/check/'})};
  const requests = [], redirects = [];
  let options, opened = false;
  const context = {URLSearchParams,
    document: {getElementById: id => elements[id], querySelector: () => ({value: 'csrf-test'})},
    window: {location: {assign: url => redirects.push(url)}, Razorpay: function(opts) {
      options = opts; this.on = () => {}; this.open = () => { opened = true; };
    }},
    fetch: async (url, request) => {
      requests.push({url, request});
      const reject = fail && url === '/verify/';
      return {ok: !reject, json: async () => url === '/order/'
        ? {key: 'public-test-key', order_id: 'order_test', amount: 105, currency: 'INR'}
        : reject ? {error: 'Signature verification failed'} : {redirect: '/invoice/1/?print=1'}};
    }
  };
  vm.runInNewContext(fs.readFileSync('static/restaurant/razorpay.js', 'utf8'), context);
  await elements['razorpay-pay'].handlers.click();
  assert.equal(opened, true);
  assert.equal(redirects.length, 0, 'Must not print before verification');
  await options.handler({razorpay_order_id: 'order_test', razorpay_payment_id: 'pay_test', razorpay_signature: 'signature'});
  assert.equal(requests[1].url, '/verify/');
  assert.equal(requests[1].request.headers['X-CSRFToken'], 'csrf-test');
  if (fail) {
    assert.equal(redirects.length, 0);
    assert.match(elements['payment-message'].textContent, /Signature verification failed/);
  } else assert.deepEqual(redirects, ['/invoice/1/?print=1']);
  assert.equal(elements['razorpay-pay'].disabled, false);
}

async function printTest() {
  let prints = 0, onLoad, historyURL;
  const context = {URL, document: {readyState: 'loading', fonts: {ready: Promise.resolve()}},
    window: {location: {href: 'https://example.test/invoice/1/?print=1'},
      history: {state: null, replaceState: (_, __, url) => {historyURL = url;}},
      print: () => {prints++;}, addEventListener: (event, handler) => {assert.equal(event, 'load'); onLoad = handler;}}
  };
  vm.runInNewContext(fs.readFileSync('static/restaurant/invoice-print.js', 'utf8'), context);
  assert.equal(prints, 0);
  await onLoad(); await onLoad();
  assert.equal(prints, 1);
  assert.equal(historyURL, 'https://example.test/invoice/1/');
}
(async () => {
  await checkoutTest(false); await checkoutTest(true); await printTest();
  console.log('PASS: button opens checkout; verified payment redirects to print; failed verification does not print; invoice prints once after load.');
})().catch(error => { console.error(error); process.exitCode = 1; });