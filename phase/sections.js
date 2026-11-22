(() => {
  "use strict";

  const phases = {
    p0: {
      label: "P0", title: "Next demo", horizon: "Delivery roadmap",
      groups: [
        ["Find & connect", ["Dashboard Finder with a sample catalogue", "Data onboarding and auto schema with user confirmation", "A working multi-table join"]],
        ["Explore & verify", ["Business view — See the answer and chart. Open SQL on demand.", "Analyst view — Inspect the SQL and result rows alongside the answer.", "Interactive charts and export", "5 difficult end-to-end questions with evidence", "5 simple questions"]],
      ],
    },
    p1: {
      label: "P1", title: "Decision platform", horizon: "Next release",
      groups: [
        ["Workforce planning", ["Headcount What-if Simulator", "Talent sourcing and portfolio dependencies"]],
        ["Investigate patterns", ["Insight Radar with statistical testing", "Why Engine with hypothesis testing"]],
      ],
    },
    p2: {
      label: "P2", title: "Future differentiation", horizon: "Delivery roadmap",
      groups: [
        ["AI assurance", ["Copilot Truth Shield"]],
      ],
    },
  };

  function renderPhase(key, container) {
    const phase = phases[key];
    const lists = phase.groups.map(([label, bullets]) =>
      `<section class="phase-scope"><h2>${label}</h2><ul>${bullets.map(text => `<li>${text}</li>`).join("")}</ul></section>`
    ).join("");
    container.innerHTML = `<section class="phase-slide">
      <header class="phase-intro">
        <p class="phase-horizon"><strong>${phase.label}</strong><span>${phase.horizon}</span></p>
        <h1>${phase.title}</h1>
      </header>
      ${lists}
    </section>`;
  }

  window.renderSection = function renderSection(key, container) {
    if (key === "question-policy") {
      container.innerHTML = `<section class="phase-slide">
        <header class="phase-intro"><h1>How Nexora handles a question</h1></header>
        <ul class="question-policy">
          <li><strong>Reuse</strong> — If a trusted dashboard already answers the question, take the user there.</li>
          <li><strong>Adapt</strong> — If the data is there but the user needs a different view, let them reshape the chart.</li>
          <li><strong>Investigate</strong> — If existing dashboards leave the question open, analyse the data and show the evidence.</li>
        </ul>
      </section>`;
    } else if (Object.hasOwn(phases, key)) renderPhase(key, container);
  };
})();
