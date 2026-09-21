(() => {
  "use strict";
  const builder = document.getElementById("recipe-builder");
  if (!builder) return;
  const costs = JSON.parse(document.getElementById("recipe-ingredient-costs").textContent);
  const halfPrice = document.getElementById("id_half_price");
  function refresh(section) {
    const portion = section.dataset.portion;
    if (portion === "half") section.hidden = !halfPrice || !halfPrice.value;
    let total = 0, count = 0;
    section.querySelectorAll(".recipe-row").forEach(row => {
      const deleted = row.querySelector('input[name$="-DELETE"]');
      if (deleted && deleted.checked) { row.hidden = true; return; }
      const ingredient = row.querySelector('select[name$="-ingredient"]');
      const quantity = row.querySelector('input[name$="-quantity_required"]');
      const info = costs[ingredient.value];
      const amount = info ? Number(info.cost) * Math.max(0, Number(quantity.value) || 0) : 0;
      row.querySelector(".ingredient-unit").textContent = info ? `Quantity in ${info.unit}; \u20b9${info.cost} per ${info.unit}` : "";
      row.querySelector(".ingredient-line-cost").textContent = info ? `\u20b9${amount.toFixed(2)}` : "--";
      if (info) { total += amount; count += 1; }
    });
    const price = Number(document.getElementById(portion === "half" ? "id_half_price" : "id_unit_price").value);
    const costLabel = count ? `\u20b9${total.toFixed(2)}` : "No recipe";
    const percentLabel = count && price > 0 ? `${(total / price * 100).toFixed(2)}%` : "--";
    section.querySelector(".recipe-total").textContent = costLabel;
    section.querySelector(".recipe-percentage").textContent = percentLabel;
    const summary = document.querySelector(`[data-recipe-summary="${portion}"]`);
    if (summary) { summary.hidden = section.hidden; summary.querySelector("output").textContent = `${costLabel} (${percentLabel})`; }
  }
  builder.querySelectorAll(".recipe-section").forEach(section => {
    section.addEventListener("input", () => refresh(section));
    section.addEventListener("change", () => refresh(section));
    section.addEventListener("click", event => {
      if (event.target.closest(".add-recipe-row")) {
        const total = section.querySelector('input[name$="-TOTAL_FORMS"]');
        if (Number(total.value) >= 100) return;
        const html = section.querySelector(".recipe-empty-row").innerHTML.replace(/__prefix__/g, total.value);
        section.querySelector(".recipe-rows").insertAdjacentHTML("beforeend", html);
        total.value = Number(total.value) + 1;
      }
      const remove = event.target.closest(".remove-recipe-row");
      if (remove) { const row = remove.closest(".recipe-row"); row.querySelector('input[name$="-DELETE"]').checked = true; row.hidden = true; }
      refresh(section);
    });
    [halfPrice, document.getElementById("id_unit_price")].filter(Boolean).forEach(input => input.addEventListener("input", () => refresh(section)));
    refresh(section);
  });
})();
