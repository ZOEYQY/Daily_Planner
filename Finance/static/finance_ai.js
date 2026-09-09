(function () {
    "use strict";

    var TIMEOUT_MS = 45000;

    function post(url, formData) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
        return fetch(url, {
            method: "POST", body: formData,
            headers: { Accept: "application/json" }, signal: controller.signal,
        })
            .then(function (r) {
                return r.json().catch(function () { return {}; })
                    .then(function (p) { return { ok: r.ok, payload: p }; });
            })
            .finally(function () { clearTimeout(timer); });
    }

    // ---------- 1. Suggest category (Add form) ----------
    var suggestBtn = document.getElementById("suggestCategoryBtn");
    if (suggestBtn) {
        suggestBtn.addEventListener("click", function () {
            var itemInput = document.querySelector('input[name="item"]');
            var typeSelect = document.getElementById("typeSelect");
            var catSelect = document.getElementById("categorySelect");
            var status = document.getElementById("suggestCategoryStatus");
            var item = ((itemInput && itemInput.value) || "").trim();
            var type = typeSelect && typeSelect.value;

            if (!type) { status.textContent = "Pick a type first."; return; }
            if (!item) { status.textContent = "Type an item first."; return; }

            suggestBtn.disabled = true;
            status.textContent = "Thinking…";
            var fd = new FormData();
            fd.append("item", item);
            fd.append("type", type);

            post(suggestBtn.dataset.url, fd)
                .then(function (res) {
                    if (!res.ok) throw new Error(res.payload.error || "Suggestion failed.");
                    var cat = res.payload.category;
                    if (!cat) { status.textContent = "No confident match — pick one yourself."; return; }
                    var has = false;
                    for (var i = 0; i < catSelect.options.length; i++) {
                        if (catSelect.options[i].value === cat) has = true;
                    }
                    if (has) {
                        catSelect.value = cat;
                        status.textContent = "Set to " + cat + " (from " + res.payload.source + ").";
                    } else {
                        status.textContent = "Suggested " + cat + ", but it isn't in your list.";
                    }
                })
                .catch(function (e) {
                    status.textContent = (e && e.name === "AbortError")
                        ? "Timed out." : (e && e.message) || "Suggestion failed.";
                })
                .finally(function () { suggestBtn.disabled = false; });
        });
    }

    // ---------- 2. Afford check (Dashboard) ----------
    var affordForm = document.getElementById("affordForm");
    if (affordForm) {
        affordForm.addEventListener("submit", function (e) {
            e.preventDefault();
            var out = document.getElementById("affordResult");
            var btn = affordForm.querySelector("button[type=submit]");
            btn.disabled = true;
            out.className = "afford-result";
            out.textContent = "Checking…";

            post(affordForm.dataset.url, new FormData(affordForm))
                .then(function (res) {
                    if (!res.ok) throw new Error(res.payload.error || "Check failed.");
                    var v = res.payload.verdict;
                    var label = v === "yes" ? "Yes" : v === "no" ? "No" : "Tight";
                    out.className = "afford-result verdict-" + v;
                    out.textContent = label + " — " + (res.payload.reasoning || "");
                })
                .catch(function (e) {
                    out.className = "afford-result verdict-no";
                    out.textContent = (e && e.name === "AbortError")
                        ? "Timed out." : (e && e.message) || "Check failed.";
                })
                .finally(function () { btn.disabled = false; });
        });
    }

    // ---------- 3. Monthly review (Summary) ----------
    var reviewBtn = document.getElementById("genReviewBtn");
    if (reviewBtn) {
        reviewBtn.addEventListener("click", function () {
            var out = document.getElementById("reviewOutput");
            reviewBtn.disabled = true;
            out.textContent = "Generating…";

            var fd = new FormData();
            fd.append("month", reviewBtn.dataset.month);
            fd.append("year", reviewBtn.dataset.year);

            post(reviewBtn.dataset.url, fd)
                .then(function (res) {
                    if (!res.ok) throw new Error(res.payload.error || "Review failed.");
                    var r = res.payload.review || {};
                    out.innerHTML = "";
                    var p = document.createElement("p");
                    p.textContent = r.narrative || "";
                    out.appendChild(p);
                    if (r.suggestions && r.suggestions.length) {
                        var ul = document.createElement("ul");
                        r.suggestions.forEach(function (s) {
                            var li = document.createElement("li");
                            li.textContent = s;
                            ul.appendChild(li);
                        });
                        out.appendChild(ul);
                    }
                    var stamp = document.createElement("small");
                    stamp.textContent = "Generated " + (r.generated_at || "just now");
                    out.appendChild(stamp);
                })
                .catch(function (e) {
                    out.textContent = (e && e.name === "AbortError")
                        ? "Timed out." : (e && e.message) || "Review failed.";
                })
                .finally(function () { reviewBtn.disabled = false; });
        });
    }

    // ---------- 4. Next-month income forecast (Summary) ----------
    var forecastBtn = document.getElementById("forecastIncomeBtn");
    if (forecastBtn) {
        forecastBtn.addEventListener("click", function () {
            var out = document.getElementById("forecastIncomeOutput");
            forecastBtn.disabled = true;
            out.textContent = "Estimating…";

            post(forecastBtn.dataset.url, new FormData())
                .then(function (res) {
                    if (!res.ok) throw new Error(res.payload.error || "Estimate failed.");
                    var f = res.payload.forecast || {};
                    out.innerHTML = "";

                    var big = document.createElement("div");
                    big.style.fontSize = "20px";
                    big.style.fontWeight = "700";
                    big.textContent = "RM " + Number(f.estimate || 0).toFixed(2);
                    var range = document.createElement("span");
                    range.style.cssText = "font-size:13px;color:var(--text-muted);font-weight:400;";
                    range.textContent = " (RM " + Number(f.low || 0).toFixed(2)
                        + " – RM " + Number(f.high || 0).toFixed(2) + ")";
                    big.appendChild(range);
                    out.appendChild(big);

                    var p = document.createElement("p");
                    p.style.margin = "6px 0";
                    p.textContent = f.reasoning || "";
                    out.appendChild(p);

                    var stamp = document.createElement("small");
                    stamp.style.color = "var(--text-faint)";
                    stamp.textContent = (f.confidence || "low") + " confidence · "
                        + (f.source === "ai" ? "AI" : "statistical")
                        + " · generated " + (f.generated_at || "just now");
                    out.appendChild(stamp);

                    forecastBtn.textContent = "✨ Re-estimate";
                })
                .catch(function (e) {
                    out.textContent = (e && e.name === "AbortError")
                        ? "Timed out." : (e && e.message) || "Estimate failed.";
                })
                .finally(function () { forecastBtn.disabled = false; });
        });
    }
}());
