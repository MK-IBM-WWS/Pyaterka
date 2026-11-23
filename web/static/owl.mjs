import * as THREE from 'three';
import { GLTFLoader } from './vendor/three/examples/jsm/loaders/GLTFLoader.js';

export async function mountOwl(stage, getVoiceLevel) {
  const {scene: owl, animations} = await new GLTFLoader().loadAsync('/avatars/snowy-owl.glb');
  const renderer = new THREE.WebGLRenderer({alpha:true, antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.65));
  renderer.setClearColor(0, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.10;
  renderer.domElement.setAttribute('aria-hidden', 'true');

  const scene = new THREE.Scene();
  scene.add(owl, new THREE.HemisphereLight(0xe0eeff, 0x60738c, 1.68));

  const light = (color, intensity, position) => {
    const lamp = new THREE.DirectionalLight(color, intensity);
    lamp.position.set(...position);
    scene.add(lamp);
  };
  light(0xfff0dc, 2.1, [-3, 5, 4]);
  light(0x9fcfff, 0.88, [4, 2, 3]);
  light(0x92c6ff, 1.65, [1, 3, -3]);

  const camera = new THREE.OrthographicCamera(-1.7, 1.7, 1.7, -1.7, .1, 30);
  camera.position.set(.27, 2.85, 7);
  camera.lookAt(0, 1.25, 0);
  const mixer = new THREE.AnimationMixer(owl);
  const jaw = owl.getObjectByName('LowerBeak');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  if (!jaw || !animations.length) throw new Error('В модели отсутствует анимация');
  const idle = mixer.clipAction(animations[0]).play();

  function resize() {
    const {width, height} = stage.getBoundingClientRect();
    if (!width || !height) return;
    const aspect = width / height;
    const halfHeight = Math.max(1.50, 1.30 / aspect);
    camera.left = -halfHeight * aspect;
    camera.right = halfHeight * aspect;
    camera.top = halfHeight;
    camera.bottom = -halfHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    renderer.render(scene, camera);
  }

  stage.append(renderer.domElement);
  const observer = new ResizeObserver(resize);
  observer.observe(stage);
  renderer.domElement.addEventListener('webglcontextlost', () => stage.classList.remove('ready'));
  renderer.domElement.addEventListener('webglcontextrestored', () => stage.classList.add('ready'));
  resize();
  stage.classList.add('ready');
  let last = performance.now();
  renderer.setAnimationLoop((now) => {
    const elapsed = Math.min((now - last) / 1000, .055);
    last = now;
    if (document.hidden || elapsed <= 0) return;
    idle.paused = reduced.matches;
    mixer.update(elapsed);
    const amount = Math.min(1, Math.max(0, getVoiceLevel()));
    const target = amount * .51;
    jaw.rotation.x += (target - jaw.rotation.x) * (1 - Math.exp(-elapsed * 29));
    renderer.render(scene, camera);
  });
}
