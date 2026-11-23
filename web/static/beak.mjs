// Each image is registered to the same feather patch and upper beak tip.
const upperTip = 79;
const halfTip = 102;
const openTip = 122;

function loadSprite(src) {
  const image = new Image();
  image.src = src;
  return image.decode().then(() => image);
}

export function mountBeak(canvas) {
  const context = canvas.getContext('2d');
  if (!context) return null;
  const {width, height} = canvas;
  let sprites;
  let openness = 0;
  let lastTick = 0;

  Promise.all([
    loadSprite('/avatars/owl-beak-half.webp'),
    loadSprite('/avatars/owl-beak-open.webp'),
  ]).then(images => { sprites = images; }, () => {});

  function paint(sprite, sourceTip, targetTip, weight) {
    if (weight <= 0) return;
    context.globalAlpha = weight;
    // Keep the upper beak and the outer border fixed while moving the lower tip.
    context.drawImage(sprite, 0, 0, width, upperTip, 0, 0, width, upperTip);
    context.drawImage(
      sprite, 0, upperTip, width, sourceTip - upperTip,
      0, upperTip, width, targetTip - upperTip,
    );
    context.drawImage(
      sprite, 0, sourceTip, width, height - sourceTip,
      0, targetTip, width, height - targetTip,
    );
  }

  function move(volume, now) {
    const elapsed = lastTick ? Math.min(now - lastTick, 80) : 16;
    lastTick = now;
    const responseTime = volume > openness ? 29 : 52;
    openness += (volume - openness) * (1 - Math.exp(-elapsed / responseTime));
    if (!sprites || openness < .035) {
      canvas.hidden = true;
      return;
    }

    const targetTip = upperTip + Math.max(1, Math.round(39 * openness));
    const wideWeight = Math.max(0, (targetTip - halfTip) / (openTip - halfTip));
    context.clearRect(0, 0, width, height);
    context.globalCompositeOperation = 'lighter';
    paint(sprites[0], halfTip, targetTip, 1 - wideWeight);
    paint(sprites[1], openTip, targetTip, wideWeight);
    canvas.style.opacity = Math.min(1, (openness - .035) / .16);
    canvas.hidden = false;
  }

  function close() {
    openness = 0;
    lastTick = 0;
    canvas.hidden = true;
  }

  return {move, close};
}
