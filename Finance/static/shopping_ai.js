(function () {
    "use strict";

    // Give up on a stuck request instead of leaving the button disabled forever.
    var ADVICE_TIMEOUT_MS = 45000;

    function recLabel(rec) {
        if (rec === "buy") return "Buy";
        if (rec === "dont_buy") return "Don't buy";
        return "Wait";
    }

    function setStatus(el, message, isError) {
        el.textContent = message;
        el.classList.toggle("is-error", Boolean(isError));
    }

    function renderAdvice(container, advice) {
        var head = [recLabel(advice.recommendation)];
        if (advice.suggested_wait_days) {
            head.push("wait ~" + advice.suggested_wait_days + " days");
        }
        if (advice.confidence) head.push(advice.confidence + " confidence");

        var card = document.createElement("div");
        card.className = "shop-advice-card";

        var rec = document.createElement("div");
        rec.className = "rec";
        rec.textContent = "AI: " + head.join(" · ");

        var body = document.createElement("p");
        body.textContent = advice.reasoning || "";

        var foot = document.createElement("small");
        foot.textContent = "Generated " + (advice.generated_at || "just now")
            + " · advice only — you decide.";

        card.appendChild(rec);
        card.appendChild(body);
        card.appendChild(foot);

        container.innerHTML = "";
        container.appendChild(card);
        container.hidden = false;
    }

    document.addEventListener("click", function (event) {
        var button = event.target.closest(".shop-advise-btn");
        if (!button) return;

        var wrap = button.closest(".shop-advice");
        var status = wrap.querySelector(".shop-advice-status");
        var result = wrap.querySelector(".shop-advice-result");
        var itemId = wrap.getAttribute("data-item");

        var originalLabel = button.textContent;
        button.disabled = true;
        button.textContent = "Thinking…";
        setStatus(status, "Asking the AI…", false);

        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, ADVICE_TIMEOUT_MS);

        var body = new FormData();
        body.append("id", itemId);

        fetch(button.dataset.adviseUrl, {
            method: "POST",
            body: body,
            headers: { Accept: "application/json" },
            signal: controller.signal,
        })
            .then(function (response) {
                return response.json()
                    .catch(function () { return {}; })
                    .then(function (payload) { return { ok: response.ok, payload: payload }; });
            })
            .then(function (res) {
                if (!res.ok) throw new Error(res.payload.error || "AI advice failed.");
                renderAdvice(result, res.payload.advice || {});
                setStatus(status, "", false);
                button.textContent = "✨ Ask AI again";
            })
            .catch(function (error) {
                var message = error && error.name === "AbortError"
                    ? "AI advice timed out. Please try again."
                    : (error && error.message) || "AI advice failed. Please try again.";
                setStatus(status, message, true);
                button.textContent = originalLabel;
            })
            .finally(function () {
                clearTimeout(timer);
                button.disabled = false;
            });
    });
}());
