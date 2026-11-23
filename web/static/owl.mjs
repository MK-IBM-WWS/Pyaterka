export function mountOwl(stage, voiceLevel) {
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  let frame = 0;
  let beak = 0;

  function moveBeak() {
    beak += (voiceLevel() - beak) * .45;
    stage.style.setProperty('--beak-open', String(beak));
    frame = requestAnimationFrame(moveBeak);
  }

  function syncBeak() {
    cancelAnimationFrame(frame);
    if (stage.dataset.state === 'speaking' && !motion.matches) {
      frame = requestAnimationFrame(moveBeak);
    } else {
      beak = 0;
      stage.style.removeProperty('--beak-open');
    }
  }

  motion.addEventListener('change', syncBeak);
  return (state) => {
    if (stage.dataset.state === state) return;
    stage.dataset.state = state;
    syncBeak();
  };
}
