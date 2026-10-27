// Small motion helpers: view transitions, confetti, count-up. All of them respect "reduce motion".

export const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Run a DOM change inside a View Transition when the browser supports it. */
export async function transition(change) {
  // A hidden tab aborts transitions with InvalidStateError, so change the DOM directly there.
  if (!document.startViewTransition || reduceMotion() || document.hidden) return change();
  let result, error;
  const vt = document.startViewTransition(async () => {
    try { result = await change(); } catch (e) { error = e; }
  });
  vt.ready.catch(() => {});
  vt.finished.catch(() => {});
  await vt.updateCallbackDone.catch(() => {});
  if (error) throw error;
  return result;
}

const COLORS = ["#ffd84d", "#ff5a8a", "#7c6cff", "#7fd8ff", "#7ee2a8", "#ffffff"];

/** A short burst of confetti from a point (defaults to the top centre). */
export function confetti(x = innerWidth / 2, y = 80, count = 90) {
  if (reduceMotion()) return;
  for (let i = 0; i < count; i++) {
    const bit = document.createElement("div");
    bit.className = "confetti";
    bit.style.background = COLORS[i % COLORS.length];
    document.body.append(bit);
    const angle = Math.random() * Math.PI * 2;
    const speed = 160 + Math.random() * 380;
    const dx = Math.cos(angle) * speed, dy = Math.sin(angle) * speed * 0.6 - 140;
    const spin = (Math.random() - 0.5) * 1440;
    bit.animate([
      { transform: `translate(${x}px, ${y}px) rotate(0deg)`, opacity: 1 },
      { transform: `translate(${x + dx * 0.6}px, ${y + dy * 0.6}px) rotate(${spin / 2}deg)`, opacity: 1, offset: 0.35 },
      { transform: `translate(${x + dx}px, ${y + dy + 520}px) rotate(${spin}deg)`, opacity: 0 },
    ], { duration: 1500 + Math.random() * 900, easing: "cubic-bezier(.2,.7,.4,1)" }).finished.then(() => bit.remove());
  }
}

/** Animate the tab underline pill to sit under the selected tab. */
export function movePill(pill, button) {
  if (!pill || !button) return;
  pill.style.left = `${button.offsetLeft}px`;
  pill.style.width = `${button.offsetWidth}px`;
}
