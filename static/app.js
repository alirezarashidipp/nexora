"use strict";

async function loadSampleQuestions() {
  const retryButton = byId("retry-samples");
  retryButton.disabled = true;
  try {
    const response = await fetch("/conversation/examples");
    if (!response.ok) throw new Error("Could not load sample questions.");
    const questions = await response.json();
    const list = byId("sample-list");
    list.replaceChildren();
    for (const question of questions) {
      const item = document.createElement("li");
      const button = element("button", question);
      button.type = "button";
      button.addEventListener("click", () => {
        if (busy || state.stage !== "new") return;
        byId("message").value = question;
        byId("message").focus();
        byId("conversation-form").requestSubmit(byId("send"));
      });
      item.append(button);
      list.append(item);
    }
    byId("sample-error").hidden = true;
  } catch {
    byId("sample-error").hidden = false;
  } finally {
    retryButton.disabled = false;
  }
}

byId("retry-samples").addEventListener("click", loadSampleQuestions);
loadSampleQuestions();
