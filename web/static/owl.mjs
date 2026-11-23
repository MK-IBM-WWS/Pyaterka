import { mountBeak } from './beak.mjs';

const scratchCycle = [
  // Milliseconds since the wing reached the temple, and the corresponding pose.
  [0, 3], [180, 4], [360, 3], [540, 4], [820, 3],
  [1090, 3], [1630, 0], [2650, 0], [3200, 3],
];

function ease(progress) {
  const t = Math.min(1, Math.max(0, progress));
  return t * t * (3 - 2 * t);
}

function scratchPose(elapsed) {
  const time = elapsed % scratchCycle.at(-1)[0];
  const next = scratchCycle.findIndex(([end]) => end > time);
  const [start, from] = scratchCycle[next - 1];
  const [end, to] = scratchCycle[next];
  return from + (to - from) * ease((time - start) / (end - start));
}

export function mountOwl(stage, voiceLevel) {
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const head = stage.querySelector('.owl-head');
  const beak = mountBeak(stage.querySelector('.owl-beak'));
  const images = [...stage.querySelectorAll('.owl-blend img')];
  const contact = images.length - 1;
  const raised = contact - 1;
  let posesReady = false;
  let frame = 0;
  let pose = 0;
  let headReturn;
  let from = 0;
  let startTime = 0;

  function drawPose(next) {
    if (pose === next) return;
    pose = next;
    stage.toggleAttribute('data-wing-raised', pose > 0);
    for (const [index, image] of images.entries()) {
      image.style.opacity = Math.max(0, 1 - Math.abs(index - pose));
    }
  }

  function tick(now) {
    const state = stage.dataset.state;
    const elapsed = Math.max(0, now - startTime);
    if (state === 'thinking' && posesReady) {
      const riseTime = 170 * Math.abs(raised - from);
      if (elapsed < riseTime) {
        drawPose(from + (raised - from) * ease(elapsed / riseTime));
      } else {
        drawPose(scratchPose(elapsed - riseTime));
      }
    } else if (pose > 0) {
      drawPose(from * (1 - ease(elapsed / (130 * from))));
    }
    if (state === 'speaking') {
      beak?.move(voiceLevel(), now);
    }
    if (state === 'speaking' || (state === 'thinking' && posesReady) || pose > 0) {
      frame = requestAnimationFrame(tick);
    }
  }

  function play(delay = 0) {
    cancelAnimationFrame(frame);
    const state = stage.dataset.state;
    if (state !== 'speaking' || motion.matches) {
      beak?.close();
    }
    if (motion.matches) {
      headReturn?.cancel();
      drawPose(state === 'thinking' && posesReady ? contact : 0);
      return;
    }
    from = pose;
    startTime = performance.now() + delay;
    frame = requestAnimationFrame(tick);
  }

  // Keep showing the neutral owl until every pose can be drawn.
  Promise.all(images.map(image => image.decode())).then(() => {
    posesReady = true;
    if (stage.dataset.state === 'thinking') play();
  }, () => {});

  motion.addEventListener('change', () => play());
  return (state) => {
    if (stage.dataset.state === state) return;
    const settleHead = state === 'thinking' && posesReady && pose === 0 && !motion.matches;
    const transform = settleHead ? getComputedStyle(head).transform : 'none';
    headReturn?.cancel();
    stage.dataset.state = state;
    if (transform !== 'none') {
      const duration = 170;
      headReturn = head.animate([{transform}, {transform: 'none'}], {duration, easing: 'ease-out'});
      play(duration);
    } else {
      play();
    }
  };
}
