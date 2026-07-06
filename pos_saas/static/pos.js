/* =========================
   POS — UI Logic (enhanced)
   ========================= */

/* ===== Helpers ===== */

function byId(id) { return document.getElementById(id); }
function q(sel, root=document) { return root.querySelector(sel); }
function qa(sel, root=document) { return Array.from(root.querySelectorAll(sel)); }

function focusLastProduct() {
  const rows = qa('#items-table tbody tr');
  if (!rows.length) return;
  const input = q('.product-input', rows[rows.length - 1]);
  if (input) input.focus();
}

/** Find product <option> by any of:
 *  - exact datalist value (the visible text)
 *  - barcode match (data-barcode)
 *  - code exact or prefix (data-code == typed || option.value starts with "CODE -")
 */
function findOption(valRaw) {
  if (!valRaw) return null;
  const val = String(valRaw).trim();
  const V = val.toUpperCase();
  const opts = qa('#products option');

  // selected catalog key from the row <select>
  let opt = opts.find(o => String(o.dataset.key || '') === val);
  if (opt) return opt;

  // selected product id from older row data
  opt = opts.find(o => (o.dataset.kind || 'product') === 'product' && String(o.dataset.id || '') === val);
  if (opt) return opt;

  // exact visible value
  opt = opts.find(o => o.value === val);
  if (opt) return opt;

  // barcode match
  opt = opts.find(o => (o.dataset.barcode || '') === val);
  if (opt) return opt;

  // code exact in data-code
  opt = opts.find(o => (o.dataset.code || '').toUpperCase() === V);
  if (opt) return opt;

  // value starts with "CODE - "
  opt = opts.find(o => o.value.toUpperCase().startsWith(V + ' - '));
  if (opt) return opt;

  return null;
}

function productOptionsHTML() {
  const opts = qa('#products option');
  const rows = ['<option value="">Select product</option>'];
  opts.forEach(o => {
    const key = o.dataset.key || `${o.dataset.kind || 'product'}:${o.dataset.id || ''}`;
    if (!key || key.endsWith(':')) return;
    const text = o.value || `Catalog item`;
    const id = o.dataset.id || '';
    const kind = o.dataset.kind || 'product';
    const code = o.dataset.code || '';
    const barcode = o.dataset.barcode || '';
    const price = o.dataset.price || '0';
    const salePrice = o.dataset.salePrice || o.dataset.price || '0';
    const tax = o.dataset.tax || '0';
    const stock = o.dataset.stock || '';
    const productId = o.dataset.productId || '';
    const batchId = o.dataset.batchId || '';
    rows.push(
      `<option value="${escapeAttr(key)}" data-key="${escapeAttr(key)}" data-kind="${escapeAttr(kind)}" data-id="${escapeAttr(id)}" data-product-id="${escapeAttr(productId)}" data-batch-id="${escapeAttr(batchId)}" data-code="${escapeAttr(code)}" data-barcode="${escapeAttr(barcode)}" data-price="${escapeAttr(price)}" data-sale-price="${escapeAttr(salePrice)}" data-tax="${escapeAttr(tax)}" data-stock="${escapeAttr(stock)}">${escapeHTML(text)}</option>`
    );
  });
  return rows.join('');
}

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function escapeAttr(value) {
  return escapeHTML(value);
}

function setProductControlValue(control, opt) {
  if (!control || !opt) return;
  const id = String(opt.dataset.key || `${opt.dataset.kind || 'product'}:${opt.dataset.id || ''}`);
  if (control.tomselect) {
    control.tomselect.setValue(id, true);
  } else {
    control.value = id;
  }
}

function getProductControlValue(control) {
  if (!control) return '';
  if (control.tomselect) return control.tomselect.getValue();
  return control.value || '';
}

function getRowLabel(row) {
  const opt = findOption(getProductControlValue(q('.product-input', row)));
  return opt ? opt.value : '';
}

function setFieldError(input, message) {
  if (!input) return;
  input.classList.add('is-invalid');
  const field = input.closest('.purchase-field') || input.parentElement;
  const feedback = q('.invalid-feedback', field);
  if (feedback) feedback.textContent = message;
}

function clearFieldError(input) {
  if (!input) return;
  input.classList.remove('is-invalid');
  const field = input.closest('.purchase-field') || input.parentElement;
  const feedback = q('.invalid-feedback', field);
  if (feedback) feedback.textContent = '';
}

function validateNonNegativeInput(input, message) {
  const raw = String(input?.value ?? '').trim();
  if (raw === '') {
    clearFieldError(input);
    return true;
  }
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0) {
    setFieldError(input, message);
    return false;
  }
  clearFieldError(input);
  return true;
}

function validatePharmacyPurchaseRow(row) {
  if (!row) return false;
  let ok = true;
  const productInput = q('.product-input', row);
  const batchInput = q('.batch-input', row);
  const manufactureInput = q('.manufacture-date-input', row);
  const expiryInput = q('.expiry-date-input', row);
  const qtyInput = q('.qty-input', row);
  const purchaseRateInput = q('.price-input', row);
  const salePriceInput = q('.sale-price-input', row);
  const mrpInput = q('.mrp-input', row);

  if (!findOption(getProductControlValue(productInput))) {
    setFieldError(productInput, 'Select a product.');
    ok = false;
  } else {
    clearFieldError(productInput);
  }

  if (!String(batchInput?.value || '').trim()) {
    setFieldError(batchInput, 'Batch number is required.');
    ok = false;
  } else {
    clearFieldError(batchInput);
  }

  if (!manufactureInput?.value) {
    setFieldError(manufactureInput, 'Manufacture date is required.');
    ok = false;
  } else {
    clearFieldError(manufactureInput);
  }

  if (!expiryInput?.value) {
    setFieldError(expiryInput, 'Expiry date is required.');
    ok = false;
  } else if (manufactureInput?.value && expiryInput.value <= manufactureInput.value) {
    setFieldError(expiryInput, 'Expiry date must be after manufacture date.');
    ok = false;
  } else {
    clearFieldError(expiryInput);
  }

  const qty = Number(qtyInput?.value || 0);
  if (!Number.isFinite(qty) || qty <= 0) {
    setFieldError(qtyInput, 'Quantity must be greater than zero.');
    ok = false;
  } else {
    clearFieldError(qtyInput);
  }

  if (!validateNonNegativeInput(purchaseRateInput, 'Purchase rate must be non-negative.')) ok = false;
  if (!validateNonNegativeInput(salePriceInput, 'Sale price must be non-negative.')) ok = false;
  if (!validateNonNegativeInput(mrpInput, 'MRP must be non-negative.')) ok = false;

  row.classList.toggle('has-validation-error', !ok);
  return ok;
}

function validatePharmacyPurchaseRows() {
  const rows = qa('#items-table tbody tr');
  let ok = rows.length > 0;
  rows.forEach(row => {
    if (!validatePharmacyPurchaseRow(row)) ok = false;
  });
  const firstInvalid = q('#items-table .is-invalid');
  if (firstInvalid) firstInvalid.focus();
  return ok;
}

/** Create a new blank line row */
function addItemRow(kind) {
  const tbody = q('#items-table tbody');
  const rowKind = kind || window.ITEMS_KIND || 'sale';
  const isPurchase = rowKind === 'purchase';
  const isPharmacyPurchase = isPurchase && !!window.PURCHASE_IS_PHARMACY;
  const isTradePurchase = isPurchase && !isPharmacyPurchase && !!window.PURCHASE_SHOW_PRICING;
  const productCell = isPharmacyPurchase
    ? `<td class="purchase-field purchase-field-product">
        <select class="form-select form-select-sm product-input js-enhance-select" data-placeholder="Select product" aria-label="Product">${productOptionsHTML()}</select>
        <div class="invalid-feedback"></div>
      </td>`
    : `<td><select class="form-select form-select-sm product-input js-enhance-select" data-placeholder="Select product">${productOptionsHTML()}</select></td>`;
  const batchCells = isPharmacyPurchase ? `
    <td class="purchase-field purchase-field-batch">
      <input type="text" class="form-control form-control-sm batch-input" placeholder="Batch number" aria-label="Batch number">
      <div class="invalid-feedback"></div>
    </td>
    <td class="purchase-field purchase-field-date">
      <input type="date" class="form-control form-control-sm manufacture-date-input" aria-label="Manufacture date">
      <div class="invalid-feedback"></div>
    </td>
    <td class="purchase-field purchase-field-date">
      <input type="date" class="form-control form-control-sm expiry-date-input" aria-label="Expiry date">
      <div class="invalid-feedback"></div>
    </td>
  ` : '';
  const priceCells = isPharmacyPurchase ? `
    <td class="purchase-field purchase-field-money">
      <input type="number" step="0.01" min="0" class="form-control form-control-sm sale-price-input text-end" placeholder="0.00" aria-label="Sale price">
      <div class="invalid-feedback"></div>
    </td>
    <td class="purchase-field purchase-field-money">
      <input type="number" step="0.01" min="0" class="form-control form-control-sm mrp-input text-end" placeholder="0.00" aria-label="MRP">
      <div class="invalid-feedback"></div>
    </td>
  ` : (isTradePurchase ? `
    <td>
      <input type="number" step="0.01" min="0" value="0" class="form-control form-control-sm sale-price-input text-end" placeholder="0.00" aria-label="Selling price">
    </td>
    <td class="profit-per-unit text-end fw-semibold">0.00</td>
  ` : '');
  const tr = document.createElement('tr');
  if (isPharmacyPurchase) tr.className = 'purchase-pharmacy-row';
  tr.innerHTML = `
    ${productCell}
    ${batchCells}
    <td class="${isPharmacyPurchase ? 'purchase-field purchase-field-qty' : ''}">
      <input type="number" min="1" value="1" class="form-control form-control-sm qty-input text-end" placeholder="Qty" aria-label="Quantity">
      ${isPharmacyPurchase ? '<div class="invalid-feedback"></div>' : ''}
    </td>
    <td class="${isPharmacyPurchase ? 'purchase-field purchase-field-money' : ''}">
      <input type="number" step="0.01" min="0" ${isPharmacyPurchase ? '' : 'value="0"'} class="form-control form-control-sm price-input text-end" placeholder="0.00" aria-label="${isPharmacyPurchase ? 'Purchase rate' : 'Cost'}">
      ${isPharmacyPurchase ? '<div class="invalid-feedback"></div>' : ''}
    </td>
    ${priceCells}
    <td class="${isPharmacyPurchase ? 'purchase-field purchase-field-total' : 'line-total text-end'}">
      ${isPharmacyPurchase ? '<div class="line-total text-end fw-semibold">0.00</div>' : '0.00'}
    </td>
    <td class="${isPharmacyPurchase ? 'purchase-field purchase-field-remove' : ''}" style="width:52px"><button type="button" class="btn btn-sm btn-outline-danger remove-row-btn" title="Remove" aria-label="Remove row">&times;</button></td>
  `;
  tbody.appendChild(tr);

  const productInput = q('.product-input', tr);
  const priceInput   = q('.price-input', tr);
  const qtyInput     = q('.qty-input', tr);
  const removeBtn    = q('button', tr);

  const applyOption = (opt) => {
    if (!opt) return;
    setProductControlValue(productInput, opt);
    if (!isPharmacyPurchase) {
      const defaultPrice = parseFloat(opt.dataset.price || '0') || 0;
      priceInput.value = defaultPrice.toFixed(2);
      if (isTradePurchase) {
        const sellingPrice = parseFloat(opt.dataset.salePrice || '0') || 0;
        const saleInput = q('.sale-price-input', tr);
        if (saleInput) saleInput.value = sellingPrice.toFixed(2);
      }
      const isBatch = (opt.dataset.kind || '') === 'batch';
      priceInput.readOnly = isBatch;
      priceInput.title = isBatch ? 'Price comes from the selected purchase batch.' : '';
    }
    if (!qtyInput.value || qtyInput.value === '0') qtyInput.value = '1';
    if (isPharmacyPurchase) validatePharmacyPurchaseRow(tr);
    recalcTotals();
  };

  productInput.addEventListener('change', () => applyOption(findOption(productInput.value)));

  qtyInput.addEventListener('input', () => {
    if (isPharmacyPurchase) validatePharmacyPurchaseRow(tr);
    recalcTotals();
  });
  priceInput.addEventListener('input', () => {
    if (isPharmacyPurchase) validatePharmacyPurchaseRow(tr);
    recalcTotals();
  });
  q('.sale-price-input', tr)?.addEventListener('input', () => {
    if (isPharmacyPurchase) validatePharmacyPurchaseRow(tr);
    recalcTotals();
  });
  q('.mrp-input', tr)?.addEventListener('input', () => { validatePharmacyPurchaseRow(tr); buildItemsJSON(); });
  q('.batch-input', tr)?.addEventListener('input', () => { validatePharmacyPurchaseRow(tr); buildItemsJSON(); });
  q('.manufacture-date-input', tr)?.addEventListener('input', () => { validatePharmacyPurchaseRow(tr); buildItemsJSON(); });
  q('.expiry-date-input', tr)?.addEventListener('input', () => { validatePharmacyPurchaseRow(tr); buildItemsJSON(); });
  removeBtn.addEventListener('click', () => { tr.remove(); recalcTotals(); });

  if (typeof window.initTomSelects === 'function') window.initTomSelects(tr);
  focusLastProduct();
}

/* ===== Totals, stock guard, & JSON ===== */

function recalcTotals() {
  const rows = qa('#items-table tbody tr');
  let subtotal = 0;
  let taxTotal = 0;

  const isPurchase = (window.ITEMS_KIND || 'sale') === 'purchase';
  const isReturn = byId('id_is_return')?.checked;

  let stockProblem = false;
  const stockErrors = [];

  rows.forEach(r => {
    const qty   = parseFloat(q('.qty-input', r)?.value || '0');
    const price = parseFloat(q('.price-input', r)?.value || '0');
    const productControl = q('.product-input', r);
    const val   = getProductControlValue(productControl);

    let taxPercent = 0;
    const matched = findOption(val);

    if (matched) {
      taxPercent = parseFloat(matched.dataset.tax || '0') || 0;

      // stock guard only for normal sales
      if (!isPurchase && !isReturn) {
        const ds = matched.dataset.stock;
        const avail = (ds !== undefined && ds !== null && ds !== '') ? parseInt(ds, 10) : null;
        if (avail !== null && qty > avail) {
          r.classList.add('table-danger');
          stockProblem = true;
          const label = matched.value || 'Selected product';
          stockErrors.push(`${label}: requested ${qty}, in stock ${avail}`);
        } else {
          r.classList.remove('table-danger');
        }
      } else {
        r.classList.remove('table-danger');
      }
    } else if (!isPurchase) {
      // unmatched product: keep row but highlight to user
      r.classList.add('table-danger');
    } else {
      r.classList.remove('table-danger');
    }

    const lt = qty * price;
    const salePriceInput = q('.sale-price-input', r);
    const profitCell = q('.profit-per-unit', r);
    if (profitCell) {
      const salePrice = parseFloat(salePriceInput?.value || '0');
      const profit = (Number.isFinite(salePrice) ? salePrice : 0) - (Number.isFinite(price) ? price : 0);
      profitCell.innerText = profit.toFixed(2);
      profitCell.classList.toggle('text-danger', profit < 0);
      profitCell.classList.toggle('text-success', profit >= 0);
    }
    subtotal += lt;
    taxTotal += (lt * taxPercent / 100.0);
    const cell = q('.line-total', r);
    if (cell) cell.innerText = lt.toFixed(2);
  });

  const discount = parseFloat(byId('id_discount')?.value || '0');
  let grand = subtotal - discount + taxTotal;

  if (isReturn) {
    subtotal = -subtotal;
    taxTotal = -taxTotal;
    grand = -grand;
  }

  const put = (sel, v) => { const n = q(sel); if (n) n.innerText = (Number.isFinite(v) ? v : 0).toFixed(2); };
  put('#subtotal', subtotal);
  put('#discount', discount);
  put('#tax', taxTotal);
  put('#grand_total', grand);

  buildItemsJSON();

  // banner + disable Complete when overselling
  const alertBox = byId('pos_error');
  const btn = byId('btnComplete');
  if (!isReturn && stockProblem) {
    if (alertBox) {
      alertBox.innerHTML = 'Not enough stock for:<br>' + stockErrors.join('<br>');
      alertBox.classList.remove('d-none');
    }
    if (btn) btn.disabled = true;
  } else {
    if (alertBox) alertBox.classList.add('d-none');
    // Do not enable yet — credit logic may still need to disable.
    if (btn) btn.disabled = false;
  }

  // Mark the global stock block so the credit UI won't re-enable by mistake
  window._pos_stock_block = !!(!isReturn && stockProblem);

  // Optional: live credit alert (defined in template). It may disable/enable the button.
  if (typeof updateCreditAlert === 'function') updateCreditAlert();

  // If stock is still a problem, keep it disabled regardless of credit logic.
  if (window._pos_stock_block && btn) btn.disabled = true;
}

function buildItemsJSON() {
  const rows = qa('#items-table tbody tr');
  const items = [];
  const isPurchase = (window.ITEMS_KIND || 'sale') === 'purchase';
  const isPharmacyPurchase = isPurchase && !!window.PURCHASE_IS_PHARMACY;

  rows.forEach(r => {
    const productControl = q('.product-input', r);
    const val = getProductControlValue(productControl);
    const opt = findOption(val);
    const qty = parseInt(q('.qty-input', r)?.value || '0', 10);
    const price = parseFloat(q('.price-input', r)?.value || '0');

    if (opt && qty > 0) {
      const pid = parseInt(opt.dataset.id, 10);
      const p = Number.isFinite(price) ? price : parseFloat(opt.dataset.price || '0') || 0;
      if (pid) {
        const kind = opt.dataset.kind || 'product';
        const catalogKey = opt.dataset.key || `${kind}:${pid}`;
        const item = { kind: kind, catalog_key: catalogKey, qty: qty, unit_price: p, price: p };
        if (kind === 'set') {
          item.set_id = pid;
        } else if (kind === 'batch') {
          item.batch_id = pid;
          item.product_id = parseInt(opt.dataset.productId || '0', 10) || null;
        } else {
          item.product_id = pid;
        }
        if (isPurchase) {
          item.purchase_item_id = r.dataset.purchaseItemId || '';
          item.product_batch_id = r.dataset.productBatchId || '';
          item.cost_price = p;
          item.purchase_rate = p;
          if (!isPharmacyPurchase && window.PURCHASE_SHOW_PRICING) {
            item.sale_price = parseFloat(q('.sale-price-input', r)?.value || '0') || 0;
          }
        }
        if (isPharmacyPurchase) {
          item.batch_no = q('.batch-input', r)?.value || '';
          item.manufacture_date = q('.manufacture-date-input', r)?.value || '';
          item.expiry_date = q('.expiry-date-input', r)?.value || '';
          item.sale_price = parseFloat(q('.sale-price-input', r)?.value || '0') || 0;
          item.mrp = parseFloat(q('.mrp-input', r)?.value || '0') || 0;
        }
        items.push(item);
      }
    }
  });

  const hidden = byId('items_json');
  if (hidden) hidden.value = JSON.stringify(items);
  return items;
}
window.buildItemsJSON = buildItemsJSON;

function removeUnselectedRows() {
  qa('#items-table tbody tr').forEach(r => {
    const productControl = q('.product-input', r);
    if (!findOption(getProductControlValue(productControl))) r.remove();
  });
}

function describeCartItems() {
  return qa('#items-table tbody tr')
    .map(r => {
      const label = getRowLabel(r);
      const qty = parseInt(q('.qty-input', r)?.value || '0', 10);
      return label && qty > 0 ? `${label} x ${qty}` : '';
    })
    .filter(Boolean);
}
window.removeUnselectedRows = removeUnselectedRows;
window.describeCartItems = describeCartItems;

/* ===== Quick scan (code/name/barcode) ===== */

function addOrBumpFromQuickScan(text) {
  const opt = findOption(text);
  if (!opt) return false;

  // If same product already in any row, bump qty instead of adding a new row
  const display = opt.value;
  const productKey = String(opt.dataset.key || `${opt.dataset.kind || 'product'}:${opt.dataset.id || ''}`);
  const rows = qa('#items-table tbody tr');
  for (const r of rows) {
    const inp = q('.product-input', r);
    const selected = findOption(getProductControlValue(inp));
    if (selected && String(selected.dataset.key || `${selected.dataset.kind || 'product'}:${selected.dataset.id || ''}`) === productKey) {
      const qtyEl = q('.qty-input', r);
      qtyEl.value = String((parseInt(qtyEl.value || '1', 10) || 1) + 1);
      recalcTotals();
      return true;
    }
  }

  // else add a row prefilled
  addItemRow('sale');
  const last = rows.length ? rows[rows.length - 1].nextElementSibling || q('#items-table tbody tr:last-child') : q('#items-table tbody tr:last-child');
  const prodInput = q('.product-input', last);
  const priceInput = q('.price-input', last);
  const qtyInput = q('.qty-input', last);

  setProductControlValue(prodInput, opt);
  const defaultPrice = parseFloat(opt.dataset.price || '0') || 0;
  if (priceInput) priceInput.value = defaultPrice.toFixed(2);
  if (qtyInput && (!qtyInput.value || qtyInput.value === '0')) qtyInput.value = '1';

  recalcTotals();
  return true;
}

/* ===== Restore on validation error ===== */

function restoreItemsFromJSON(jsonStr) {
  let arr = [];
  try { arr = JSON.parse(jsonStr || '[]'); } catch (_) { arr = []; }
  const tbody = q('#items-table tbody');
  if (!tbody || !arr.length) return false;

  tbody.innerHTML = '';
  arr.forEach(item => {
    addItemRow(window.ITEMS_KIND || 'sale');
    const tr = q('#items-table tbody tr:last-child');
    tr.dataset.purchaseItemId = item.purchase_item_id || '';
    tr.dataset.productBatchId = item.product_batch_id || '';
    const kind = item.kind || (item.batch_id ? 'batch' : (item.set_id ? 'set' : 'product'));
    const lookupId = kind === 'set' ? item.set_id : (kind === 'batch' ? item.batch_id : item.product_id);
    const opt = q(`#products option[data-kind="${kind}"][data-id="${lookupId}"]`);
    const displayValue = opt ? opt.value : '';
    const prodInput = q('.product-input', tr);
    const qtyInput  = q('.qty-input', tr);
    const prInput   = q('.price-input', tr);

    if (prodInput && opt) setProductControlValue(prodInput, opt);
    if (qtyInput)  qtyInput.value = parseInt(item.qty || 1, 10);
    const p = (item.unit_price ?? item.price ?? 0);
    if (prInput)   prInput.value = (typeof p === 'number') ? p.toFixed(2) : String(p);
    if (prInput && kind === 'batch') {
      prInput.readOnly = true;
      prInput.title = 'Price comes from the selected purchase batch.';
    }
    const batchInput = q('.batch-input', tr);
    const manufactureDateInput = q('.manufacture-date-input', tr);
    const expiryDateInput = q('.expiry-date-input', tr);
    const salePriceInput = q('.sale-price-input', tr);
    const mrpInput = q('.mrp-input', tr);
    if (batchInput) batchInput.value = item.batch_no || '';
    if (manufactureDateInput) manufactureDateInput.value = item.manufacture_date || '';
    if (expiryDateInput) expiryDateInput.value = item.expiry_date || '';
    if (salePriceInput) salePriceInput.value = String(item.sale_price ?? 0);
    if (mrpInput) mrpInput.value = String(item.mrp ?? 0);
  });

  recalcTotals();
  return true;
}

/* ===== Boot ===== */

document.addEventListener('DOMContentLoaded', function () {
  const purchaseForm = byId('purchase-form');

  // restore rows after validation error
  if (!purchaseForm) {
    let restored = false;
    const hidden = byId('items_json');
    if (hidden && hidden.value && hidden.value.trim().length > 2) {
      try { restored = restoreItemsFromJSON(hidden.value); } catch (_) { restored = false; }
    }
    if (!restored && !q('#items-table tbody tr')) addItemRow(window.ITEMS_KIND || 'sale');
  }

  // quick scan focus (F2)
  document.addEventListener('keydown', function(e){
    if (e.key === 'F2') {
      const qs = byId('quick_scan');
      if (qs) { e.preventDefault(); qs.focus(); qs.select?.(); }
    }
  });

  // quick scan enter -> add
  const qs = byId('quick_scan');
  if (qs) {
    qs.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (addOrBumpFromQuickScan(qs.value.trim())) {
          qs.value = '';
        } else {
          // gentle nudge if not found
          const box = byId('pos_error');
          if (box) { box.textContent = 'No matching product: try full code or scan barcode.'; box.classList.remove('d-none'); }
        }
      }
    });
    qs.focus();
  }

  // add/clear buttons
  const btnAdd = byId('btnAddRow');
  if (btnAdd) btnAdd.addEventListener('click', () => addItemRow('sale'));

  const btnClear = byId('btnClear');
  if (btnClear) btnClear.addEventListener('click', () => {
    const tbody = q('#items-table tbody');
    tbody.innerHTML = '';
    recalcTotals();
    if (qs) qs.focus();
  });

  // submit guard
  const form = byId('pos-form');
  if (form) {
    form.addEventListener('submit', function (ev) {
      const items = buildItemsJSON(); // ensure fresh JSON
      if (!items.length) {
        ev.preventDefault();
        const box = byId('pos_error');
        if (box) { box.textContent = 'Add at least one item before completing the sale.'; box.classList.remove('d-none'); }
        else alert('Add at least one item before completing the sale.');
        return;
      }
      // Block if stock guard flagged a problem
      if (window._pos_stock_block) {
        ev.preventDefault();
        const box = byId('pos_error');
        if (box) { box.textContent = 'Cannot proceed: not enough stock for one or more items.'; box.classList.remove('d-none'); }
        return;
      }
    });
  }

  if (purchaseForm) {
    purchaseForm.addEventListener('submit', function (ev) {
      if (window.PURCHASE_IS_PHARMACY && !validatePharmacyPurchaseRows()) {
        ev.preventDefault();
        return;
      }
      const items = buildItemsJSON();
      if (!items.length) {
        ev.preventDefault();
        alert('Add at least one item before saving the purchase.');
      }
    });
  }

  // react to totals-affecting inputs
  ['#id_discount', '#id_is_return'].forEach(sel => {
    const el = q(sel);
    if (el) el.addEventListener('input', recalcTotals);
  });

  // credit-related live listeners
  const paid = byId('id_paid_amount');
  paid?.addEventListener('input', function(){ if (typeof updateCreditAlert === 'function') updateCreditAlert(); });

  // try common ways to reference the customer select
  const cust = byId('id_customer') || q('select[name="customer"]');
  cust?.addEventListener('change', function(){ if (typeof updateCreditAlert === 'function') updateCreditAlert(); });

  // initial compute
  recalcTotals();
});

/* ===== Universal barcode-scanner listener (keyboard wedge) ===== */
// Treat very fast consecutive keystrokes as a barcode scan.
// Works even when focus isn't on the Quick Scan box.
(function () {
  const scanner = {
    buf: '',
    lastTs: 0,
    timeout: null,
    // Tweaks:
    minLength: 6,    // ignore short bursts
    maxGap: 35,      // ms between keys to still consider "scanner speed"
    idleCommit: 120, // if no key within this time, commit as a scan (for scanners without Enter/Tab)
  };

  function commitScan() {
    const text = scanner.buf.trim();
    scanner.buf = '';
    if (!text || text.length < scanner.minLength) return;

    // Reflect in the visible box (optional)
    const qs = document.getElementById('quick_scan');
    if (qs) qs.value = text;

    if (typeof addOrBumpFromQuickScan === 'function') {
      const ok = addOrBumpFromQuickScan(text);
      if (ok && qs) qs.value = '';
      if (!ok) {
        const box = document.getElementById('pos_error');
        if (box) {
          box.textContent = 'No matching product for scanned code: ' + text;
          box.classList.remove('d-none');
        }
      }
    }
  }

  function onKeyDown(e) {
    // ignore modifiers & IME
    if (e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return;

    const k = e.key;
    const ts = e.timeStamp || Date.now();
    const gap = ts - (scanner.lastTs || ts);
    scanner.lastTs = ts;

    // If the gap is big, start a new buffer (user typing)
    if (gap > scanner.maxGap) {
      scanner.buf = '';
    }

    if (k === 'Enter' || k === 'NumpadEnter' || k === 'Tab') {
      // Many scanners send Enter or Tab as suffix
      if (scanner.buf.length >= scanner.minLength) {
        e.preventDefault();
        commitScan();
      }
      return;
    }

    // Printable char? (letters, digits, common symbols)
    if (k.length === 1) {
      scanner.buf += k;

      // Idle fallback for scanners that send no suffix
      clearTimeout(scanner.timeout);
      scanner.timeout = setTimeout(commitScan, scanner.idleCommit);
      return;
    }

    // Ignore other keys (arrows, backspace, etc.) for scanner flow
  }

  // Global capture so it works even if quick_scan isn't focused
  document.addEventListener('keydown', onKeyDown, true);
})();
