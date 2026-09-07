(function () {
    "use strict";

    // Give up on a stuck request instead of leaving the button disabled forever.
    var ANALYSIS_TIMEOUT_MS = 45000;

    function setStatus(status, message, isError) {
        status.textContent = message;
        status.classList.toggle("receipt-analysis-error", Boolean(isError));
        status.classList.toggle("receipt-analysis-success", !isError && Boolean(message));
    }

    function initReceiptAssistant({ applyAmount, applyCategory } = {}) {
        const receiptInput = document.getElementById("receiptInput");
        const analyzeButton = document.getElementById("receiptAnalyzeButton");
        const status = document.getElementById("receiptAnalysisStatus");
        if (!receiptInput || !analyzeButton || !status) return;

        analyzeButton.addEventListener("click", async function () {
            const receipt = receiptInput.files && receiptInput.files[0];
            if (!receipt) {
                setStatus(status, "Choose a receipt image before using AI extraction.", true);
                return;
            }

            const originalLabel = analyzeButton.textContent.trim();
            analyzeButton.disabled = true;
            analyzeButton.textContent = "Reading receipt…";
            setStatus(status, "Analyzing the image…", false);

            const controller = new AbortController();
            const timer = setTimeout(function () { controller.abort(); }, ANALYSIS_TIMEOUT_MS);

            try {
                const formData = new FormData();
                formData.append("receipt", receipt);
                const response = await fetch(analyzeButton.dataset.analyzeUrl, {
                    method: "POST",
                    body: formData,
                    headers: { Accept: "application/json" },
                    signal: controller.signal,
                });
                const payload = await response.json().catch(function () { return {}; });
                if (!response.ok) throw new Error(payload.error || "Receipt analysis failed.");

                const fields = payload.fields || {};
                const dateInput = document.querySelector('input[name="date"]');
                const itemInput = document.querySelector('input[name="item"]');
                const applied = [];

                if (fields.merchant && itemInput) {
                    itemInput.value = fields.merchant;
                    applied.push("merchant");
                }
                if (fields.date && dateInput) {
                    dateInput.value = fields.date;
                    applied.push("date");
                }
                const total = Number(fields.total);
                if (Number.isFinite(total) && total > 0 && typeof applyAmount === "function") {
                    applyAmount(total);
                    applied.push("total");
                }
                if (
                    fields.category &&
                    typeof applyCategory === "function" &&
                    applyCategory(fields.category)
                ) {
                    applied.push("category");
                }

                const detail = applied.length
                    ? ` Applied ${applied.join(", ")}.`
                    : " No reliable fields found.";
                setStatus(
                    status,
                    `AI extraction complete (${fields.confidence || "low"} confidence).${detail} Review before saving.`,
                    false
                );
            } catch (error) {
                const message = error && error.name === "AbortError"
                    ? "Receipt analysis timed out. You can still enter the details manually."
                    : (error && error.message) || "Receipt analysis failed. You can still enter the details manually.";
                setStatus(status, message, true);
            } finally {
                clearTimeout(timer);
                analyzeButton.disabled = false;
                analyzeButton.textContent = originalLabel;
            }
        });
    }

    window.initReceiptAssistant = initReceiptAssistant;
}());
