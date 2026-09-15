// Aba Tutorial: passos com progresso salvo neste navegador.

const KEY = "aurum.tutorial";
const RING = 326.7;

function loadDone() {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY)) || []);
  } catch {
    return new Set();
  }
}

function saveDone(done) {
  try {
    localStorage.setItem(KEY, JSON.stringify([...done]));
  } catch {
    /* armazenamento indisponível: o progresso vale só nesta sessão */
  }
}

export function setupTutorial() {
  const steps = [...document.querySelectorAll(".tut-step")];
  const nav = document.getElementById("tut-nav");
  const done = loadDone();

  nav.innerHTML = steps.map((step) => {
    const title = step.querySelector("h3").textContent;
    return `<a href="#passo-${step.dataset.step}" data-step="${step.dataset.step}">${step.dataset.step}. ${title}</a>`;
  }).join("");

  const refresh = () => {
    for (const step of steps) {
      const isDone = done.has(step.dataset.step);
      step.classList.toggle("done", isDone);
      step.querySelector(".tut-done input").checked = isDone;
      nav.querySelector(`[data-step="${step.dataset.step}"]`).classList.toggle("done", isDone);
    }
    const pct = Math.round((done.size / steps.length) * 100);
    document.getElementById("tut-pct").textContent = `${pct}%`;
    document.querySelector("#tut-ring .ring-fill").style.strokeDashoffset = RING * (1 - pct / 100);
  };

  for (const step of steps) {
    step.querySelector(".tut-done input").addEventListener("change", (e) => {
      if (e.target.checked) done.add(step.dataset.step);
      else done.delete(step.dataset.step);
      saveDone(done);
      refresh();
    });
  }
  nav.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-step]");
    if (!link) return;
    e.preventDefault();  // não troca o hash (ele guarda o ativo aberto)
    document.getElementById(`passo-${link.dataset.step}`).scrollIntoView({ behavior: "smooth", block: "start" });
  });
  refresh();
}
